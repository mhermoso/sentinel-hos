"""ARQ background worker for continuous telematics feed polling.

Runs as a standalone process via ``arq app.domains.ingestion.poller.WorkerSettings``.
Every HOS poll cycle:
  1. Loads the last cursor from Redis.
  2. Calls the Geotab adapter to fetch new DutyStatusLog records.
  3. Normalises and hashes the records.
  4. Persists to PostgreSQL and updates Redis state.
  5. Saves the new cursor for the next cycle.

A separate cron polls Geotab LogRecord GPS breadcrumbs (ADR-007) into
``gps_breadcrumbs`` with cursor ``cursor:geotab-logrecord:{tenant_id}``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from pydantic import ValidationError

from app.core.config import settings
from app.core.database import async_session_factory, init_db
from app.core.ops_log import configure_ops_log
from app.core.redis import init_redis
from app.domains.engine.sweeper import sweep_active_drivers
from app.domains.ingestion.adapters.geotab import (
    GeotabAdapter,
    map_geotab_log_record_to_breadcrumb,
)
from app.domains.ingestion.normalizer import normalize_batch
from app.domains.ingestion.repository import IngestionRepository
from app.domains.ingestion.schemas import DCWGpsBreadcrumb

logger = logging.getLogger("dcw.ingestion.poller")

# Module-level adapter instance (initialised on worker startup)
_geotab_adapter: GeotabAdapter | None = None

DEFAULT_CURSOR = "0000000000000000"
LOG_RECORD_PROVIDER = "geotab-logrecord"


async def startup(ctx: dict[str, Any]) -> None:
    """ARQ worker startup hook — initialise DB, Redis, and Geotab adapter."""
    global _geotab_adapter

    configure_ops_log(process_name="worker")
    logger.info("Starting DCW ingestion worker…")
    await init_db()
    await init_redis()

    if settings.GEOTAB_DATABASE and settings.GEOTAB_USERNAME and settings.GEOTAB_PASSWORD:
        try:
            _geotab_adapter = GeotabAdapter()
            await _geotab_adapter.connect()
            ctx["geotab_adapter"] = _geotab_adapter
            logger.info("Geotab adapter connected")
        except Exception as exc:
            logger.error("Geotab adapter connection failed: %s — poller will idle", exc)
            ctx["geotab_adapter"] = None
    else:
        logger.warning(
            "Geotab credentials not configured — ingestion poller will idle until "
            "GEOTAB_DATABASE, GEOTAB_USERNAME, and GEOTAB_PASSWORD are set"
        )
        ctx["geotab_adapter"] = None

    logger.info("DCW ingestion worker ready")


async def shutdown(ctx: dict[str, Any]) -> None:
    """ARQ worker shutdown hook — clean up resources."""
    logger.info("Shutting down DCW ingestion worker")


async def poll_geotab_feed(ctx: dict[str, Any]) -> dict[str, Any]:
    """ARQ cron task — poll Geotab for new HOS DutyStatusLog records.

    Orchestrates: fetch → normalise → hash → persist → update Redis.
    """
    adapter: GeotabAdapter | None = ctx.get("geotab_adapter")
    if adapter is None:
        logger.debug("Geotab adapter not configured — skipping poll cycle")
        return {"records_fetched": 0, "skipped": True}

    tenant_id = settings.GEOTAB_DATABASE

    # 1. Load cursor
    cursor = await IngestionRepository.load_cursor("geotab", tenant_id)
    if cursor is None:
        cursor = DEFAULT_CURSOR

    # 2. Fetch from Geotab
    raw_logs, next_cursor = await adapter.fetch_feed(
        tenant_id=tenant_id,
        from_cursor=cursor,
    )

    if not raw_logs:
        logger.info("No new Geotab records (cursor=%s)", cursor)
        # Still save cursor in case toVersion advanced
        await IngestionRepository.save_cursor("geotab", tenant_id, next_cursor)
        return {"records_fetched": 0, "cursor": next_cursor}

    # 3. Normalise
    normalised_logs = normalize_batch(raw_logs)

    # 4. Persist to PostgreSQL
    async with async_session_factory() as session:
        repo = IngestionRepository(session)
        inserted = await repo.persist_canonical_logs(normalised_logs)
        await session.commit()

    # 5. Update Redis active driver set
    driver_ids = {log.driver_id for log in normalised_logs}
    await IngestionRepository.update_active_drivers(driver_ids)

    # 6. Save cursor
    await IngestionRepository.save_cursor("geotab", tenant_id, next_cursor)

    logger.info(
        "Poll cycle complete: %d fetched, %d inserted, %d drivers active, cursor=%s",
        len(raw_logs),
        inserted,
        len(driver_ids),
        next_cursor,
    )

    return {
        "records_fetched": len(raw_logs),
        "records_inserted": inserted,
        "drivers": list(driver_ids),
        "cursor": next_cursor,
    }


async def poll_geotab_log_records(ctx: dict[str, Any]) -> dict[str, Any]:
    """ARQ cron task — poll Geotab LogRecord GPS breadcrumbs (ADR-007)."""
    adapter: GeotabAdapter | None = ctx.get("geotab_adapter")
    if adapter is None:
        logger.debug("Geotab adapter not configured — skipping LogRecord poll")
        return {"records_fetched": 0, "skipped": True}

    tenant_id = settings.GEOTAB_DATABASE
    cursor = await IngestionRepository.load_cursor(LOG_RECORD_PROVIDER, tenant_id)
    if cursor is None:
        cursor = DEFAULT_CURSOR

    raw_records, next_cursor = await adapter.fetch_log_record_feed(
        tenant_id=tenant_id,
        from_cursor=cursor,
    )

    if not raw_records:
        logger.info("No new Geotab LogRecords (cursor=%s)", cursor)
        await IngestionRepository.save_cursor(
            LOG_RECORD_PROVIDER, tenant_id, next_cursor
        )
        return {"records_fetched": 0, "cursor": next_cursor}

    breadcrumbs: list[DCWGpsBreadcrumb] = []
    device_driver_cache: dict[str, str] = {}

    async with async_session_factory() as session:
        repo = IngestionRepository(session)
        for record in raw_records:
            record_id = record.get("id", "UNKNOWN_ID")
            try:
                device = record.get("device")
                if isinstance(device, dict):
                    device_id = str(device.get("id") or "")
                elif isinstance(device, str):
                    device_id = device
                else:
                    device_id = ""
                if not device_id:
                    logger.warning("LogRecord %s missing device — skipped", record_id)
                    continue

                event_ts = record.get("dateTime")
                if isinstance(event_ts, str):
                    event_ts = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
                if isinstance(event_ts, datetime) and event_ts.tzinfo is None:
                    event_ts = event_ts.replace(tzinfo=UTC)
                if not isinstance(event_ts, datetime):
                    logger.warning("LogRecord %s missing dateTime — skipped", record_id)
                    continue

                driver_id = await repo.resolve_driver_for_device(
                    tenant_id=tenant_id,
                    device_id=device_id,
                    as_of=event_ts,
                    cache=device_driver_cache,
                )
                crumb = map_geotab_log_record_to_breadcrumb(
                    record, tenant_id=tenant_id, driver_id=driver_id
                )
                breadcrumbs.append(crumb)
            except ValidationError as ve:
                logger.warning(
                    "Validation failed for LogRecord %s: %s",
                    record_id,
                    ve.errors(),
                )
            except Exception as exc:
                logger.warning(
                    "Unexpected LogRecord parse failure for %s: %s",
                    record_id,
                    exc,
                )

        inserted = await repo.persist_gps_breadcrumbs(breadcrumbs)
        await session.commit()

    await IngestionRepository.save_cursor(LOG_RECORD_PROVIDER, tenant_id, next_cursor)

    logger.info(
        "LogRecord poll complete: %d fetched, %d mapped, %d inserted, cursor=%s",
        len(raw_records),
        len(breadcrumbs),
        inserted,
        next_cursor,
    )
    return {
        "records_fetched": len(raw_records),
        "records_mapped": len(breadcrumbs),
        "records_inserted": inserted,
        "cursor": next_cursor,
    }


# ── ARQ Worker Settings ─────────────────────────────────────────────────


class WorkerSettings:
    """Configuration class consumed by ``arq`` CLI runner.

    Usage: ``arq app.domains.ingestion.poller.WorkerSettings``
    """

    functions = [poll_geotab_feed, poll_geotab_log_records, sweep_active_drivers]
    cron_jobs = [
        cron(
            poll_geotab_feed,
            second={0},  # Every 2 minutes (controlled by POLL_INTERVAL_SECONDS)
            run_at_startup=True,
        ),
        cron(
            poll_geotab_log_records,
            second={15},  # Offset from HOS poll; same ~2 min cadence
            run_at_startup=True,
        ),
        cron(
            sweep_active_drivers,
            second={30},  # Runs 30s after each poll cycle
            run_at_startup=False,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        database=settings.REDIS_DB,
    )
    max_jobs = 5
    job_timeout = 300  # 5-minute timeout per poll cycle
