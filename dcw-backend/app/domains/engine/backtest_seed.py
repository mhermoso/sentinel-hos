"""One-shot backtest seed: Postgres HOS → event backtest → Redis dispatches."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.redis import get_redis
from app.domains.dashboard.driver_names import load_driver_names_from_redis
from app.domains.engine.backtest_runner import (
    backtest_dispatches_key,
    bootstrap_backtest_key,
    build_driver_name_map,
    run_backtest,
    serialize_dispatch_payload,
)
from app.domains.ingestion.models import CanonicalHOSLogRecord
from app.domains.ingestion.schemas import CanonicalDutyStatus, DCWCanonicalHOSLog

logger = logging.getLogger("dcw.engine.backtest_seed")


def record_to_canonical(record: CanonicalHOSLogRecord) -> DCWCanonicalHOSLog:
    """Map an ORM row to the canonical HOS schema."""
    return DCWCanonicalHOSLog(
        tenant_id=record.tenant_id,
        driver_id=record.driver_id,
        driver_name=record.driver_name,
        raw_id=record.raw_id,
        status=CanonicalDutyStatus(record.status),
        event_timestamp=record.event_timestamp,
        device_id=record.device_id,
        latitude=record.latitude,
        longitude=record.longitude,
        odometer_km=record.odometer_km,
        annotation=record.annotation,
        raw_payload=record.raw_payload,
        inputs_hash=record.inputs_hash,
    )


async def load_grouped_hos_from_postgres(
    tenant_id: str,
    days: int,
) -> dict[str, list[DCWCanonicalHOSLog]]:
    """Load last ``days`` of HOS logs grouped by driver_id."""
    since = datetime.now(UTC) - timedelta(days=days)
    grouped: dict[str, list[DCWCanonicalHOSLog]] = {}

    async with async_session_factory() as session:
        stmt = (
            select(CanonicalHOSLogRecord)
            .where(
                CanonicalHOSLogRecord.tenant_id == tenant_id,
                CanonicalHOSLogRecord.event_timestamp >= since,
            )
            .order_by(
                CanonicalHOSLogRecord.driver_id,
                CanonicalHOSLogRecord.event_timestamp.asc(),
            )
        )
        result = await session.execute(stmt)
        for record in result.scalars().all():
            grouped.setdefault(record.driver_id, []).append(record_to_canonical(record))

    return grouped


async def maybe_run_backtest_seed() -> dict[str, Any] | None:
    """Run event-mode backtest once per tenant and store dispatches in Redis.

    Returns a summary dict when the seed ran, or ``None`` when skipped.
    """
    if not settings.BACKTEST_SEED_ON_STARTUP:
        logger.info("BACKTEST_SEED_ON_STARTUP=false — skipping backtest seed")
        return None

    tenant_id = settings.GEOTAB_DATABASE
    if not tenant_id:
        logger.warning("GEOTAB_DATABASE unset — skipping backtest seed")
        return None

    days = settings.BACKTEST_SEED_DAYS
    flag_key = bootstrap_backtest_key(tenant_id, days)
    redis = await get_redis()

    claimed = await redis.set(flag_key, "running", nx=True)
    if not claimed:
        existing = await redis.get(flag_key)
        logger.info("Backtest seed already claimed (%s=%s) — skipping", flag_key, existing)
        return None

    try:
        grouped = await load_grouped_hos_from_postgres(tenant_id, days)
        if not grouped:
            logger.warning(
                "No HOS logs in Postgres for tenant=%s last %d days — skipping backtest seed",
                tenant_id,
                days,
            )
            await redis.delete(flag_key)
            return None

        redis_names = await load_driver_names_from_redis(tenant_id)
        driver_names = build_driver_name_map(grouped, redis_names=redis_names)
        total_events = sum(len(logs) for logs in grouped.values())
        logger.info(
            "Running backtest seed: tenant=%s days=%d drivers=%d events=%d",
            tenant_id,
            days,
            len(grouped),
            total_events,
        )

        result = run_backtest(
            grouped,
            mode="event",
            interval_seconds=settings.POLL_INTERVAL_SECONDS,
            driver_names=driver_names,
        )
        payload = serialize_dispatch_payload(result)
        dispatches_key = backtest_dispatches_key(tenant_id)
        await redis.set(dispatches_key, json.dumps(payload))

        await redis.set(flag_key, "done")
        summary = {
            "tenant_id": tenant_id,
            "days": days,
            "driver_count": len(grouped),
            "total_events": total_events,
            "would_dispatch_count": result["summary"]["would_dispatch_count"],
            "dispatches_key": dispatches_key,
        }
        logger.info("Backtest seed complete: %s", summary)
        return summary
    except Exception:
        await redis.delete(flag_key)
        logger.exception("Backtest seed failed — Redis flag cleared for retry")
        raise
