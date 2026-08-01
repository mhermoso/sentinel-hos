"""One-shot Geotab history backfill for the last N days.

GetFeed walks the entire retained feed from ``fromVersion=0``, which can leave
dev environments with sparse recent coverage while the cursor sits at the tip.
This module uses date-range ``Get`` (DutyStatusLog + day-paged LogRecord) to
ensure the configured lookback window is present, then advances both feed
cursors to the live tip so the ARQ poller continues incrementally.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import mygeotab
import mygeotab.serializers as geo_serializers

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.redis import get_redis
from app.domains.ingestion.adapters.geotab import (
    GeotabAdapter,
    map_geotab_log_record_to_breadcrumb,
    map_geotab_log_to_canonical,
)
from app.domains.ingestion.normalizer import normalize_batch
from app.domains.ingestion.repository import IngestionRepository
from app.domains.ingestion.schemas import DCWCanonicalHOSLog, DCWGpsBreadcrumb

logger = logging.getLogger("dcw.ingestion.history_backfill")

LOG_RECORD_PROVIDER = "geotab-logrecord"
_GEOTAB_GET_SOFT_CAP = 50_000


def bootstrap_key(tenant_id: str, days: int) -> str:
    """Redis flag: set when a successful N-day backfill has completed."""
    return f"bootstrap:geotab-history:{days}d:v1:{tenant_id}"


def _plain_record(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return json.loads(geo_serializers.json_serialize(raw))


def _build_driver_name_map(api: mygeotab.API) -> dict[str, str]:
    users = api.get("User")
    name_map: dict[str, str] = {}
    for user in users:
        uid = user.get("id")
        if not uid:
            continue
        first = user.get("firstName", "") or ""
        last = user.get("lastName", "") or ""
        full_name = f"{first} {last}".strip() or user.get("name", uid)
        name_map[str(uid)] = full_name
    return name_map


async def _advance_feed_tip(
    api: mygeotab.API,
    *,
    type_name: str,
    provider: str,
    tenant_id: str,
) -> str:
    """Omit fromVersion so Geotab returns the live tip (empty batch + toVersion)."""
    previous = await IngestionRepository.load_cursor(provider, tenant_id)
    feed_response = await asyncio.to_thread(api.call, "GetFeed", typeName=type_name)
    tip = str(feed_response.get("toVersion", ""))
    if not tip:
        raise RuntimeError(f"GetFeed({type_name}) returned no toVersion")
    await IngestionRepository.save_cursor(provider, tenant_id, tip)
    logger.info(
        "Advanced cursor:%s:%s previous=%s tip=%s",
        provider,
        tenant_id,
        previous,
        tip,
    )
    return tip


def _fetch_log_records_window(
    api: mygeotab.API,
    from_dt: datetime,
    to_dt: datetime,
) -> list[Any]:
    from_iso = from_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to_iso = to_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    raw_logs = api.get("LogRecord", search={"fromDate": from_iso, "toDate": to_iso})
    count = len(raw_logs)
    span = to_dt - from_dt
    if count >= _GEOTAB_GET_SOFT_CAP and span > timedelta(minutes=15):
        mid = from_dt + span / 2
        logger.warning(
            "LogRecord window %s→%s returned %d rows (soft cap) — splitting",
            from_iso,
            to_iso,
            count,
        )
        return _fetch_log_records_window(api, from_dt, mid) + _fetch_log_records_window(
            api, mid, to_dt
        )
    logger.info("Fetched %d LogRecord rows for %s → %s", count, from_iso, to_iso)
    return list(raw_logs)


async def _backfill_hos(api: mygeotab.API, tenant_id: str, days: int) -> dict[str, int]:
    now = datetime.now(UTC)
    from_date = now - timedelta(days=days)
    from_iso = from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    logger.info("Backfilling DutyStatusLog from %s (%d days)", from_iso, days)

    driver_names = await asyncio.to_thread(_build_driver_name_map, api)
    raw_logs = await asyncio.to_thread(
        api.get, "DutyStatusLog", search={"fromDate": from_iso}
    )
    logger.info("Retrieved %d raw DutyStatusLog records", len(raw_logs))

    canonical: list[DCWCanonicalHOSLog] = []
    failed = 0
    for raw in raw_logs:
        try:
            plain = _plain_record(raw)
            driver_id = str(
                ((plain.get("driver") or {}) if isinstance(plain.get("driver"), dict) else {}).get(
                    "id", "UNKNOWN_DRIVER"
                )
            )
            canonical.append(
                map_geotab_log_to_canonical(
                    plain,
                    tenant_id=tenant_id,
                    driver_name=driver_names.get(driver_id),
                )
            )
        except Exception as exc:
            failed += 1
            logger.warning("Failed to map DutyStatusLog: %s", exc)

    normalised = normalize_batch(canonical)
    async with async_session_factory() as session:
        repo = IngestionRepository(session)
        inserted = await repo.persist_canonical_logs(normalised)
        driver_ids = {log.driver_id for log in normalised}
        await IngestionRepository.update_active_drivers(driver_ids)
        await session.commit()

    return {
        "fetched": len(raw_logs),
        "mapped": len(normalised),
        "inserted": inserted,
        "failed": failed,
    }


async def _backfill_gps(api: mygeotab.API, tenant_id: str, days: int) -> dict[str, int]:
    now = datetime.now(UTC)
    from_date = now - timedelta(days=days)
    logger.info("Backfilling LogRecord GPS from %s (%d days, day-paged)", from_date.isoformat(), days)

    mapped_total = 0
    inserted_total = 0
    failed_total = 0
    device_driver_cache: dict[str, str] = {}
    cursor = from_date

    while cursor < now:
        window_end = min(cursor + timedelta(days=1), now)
        raw_logs = await asyncio.to_thread(_fetch_log_records_window, api, cursor, window_end)
        breadcrumbs: list[DCWGpsBreadcrumb] = []

        async with async_session_factory() as session:
            repo = IngestionRepository(session)
            for raw in raw_logs:
                plain: dict[str, Any] = {}
                try:
                    plain = _plain_record(raw)
                    device = plain.get("device")
                    if isinstance(device, dict):
                        device_id = str(device.get("id") or "")
                    elif isinstance(device, str):
                        device_id = device
                    else:
                        device_id = ""
                    if not device_id:
                        failed_total += 1
                        continue

                    event_ts = plain.get("dateTime")
                    if isinstance(event_ts, str):
                        event_ts = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
                    if isinstance(event_ts, datetime) and event_ts.tzinfo is None:
                        event_ts = event_ts.replace(tzinfo=UTC)
                    if not isinstance(event_ts, datetime):
                        failed_total += 1
                        continue

                    driver_id = await repo.resolve_driver_for_device(
                        tenant_id=tenant_id,
                        device_id=device_id,
                        as_of=event_ts,
                        cache=device_driver_cache,
                    )
                    breadcrumbs.append(
                        map_geotab_log_record_to_breadcrumb(
                            plain, tenant_id=tenant_id, driver_id=driver_id
                        )
                    )
                except Exception as exc:
                    failed_total += 1
                    logger.warning("Failed to map LogRecord %s: %s", plain.get("id"), exc)

            inserted = await repo.persist_gps_breadcrumbs(breadcrumbs)
            await session.commit()

        mapped_total += len(breadcrumbs)
        inserted_total += inserted
        cursor = window_end

    return {
        "mapped": mapped_total,
        "inserted": inserted_total,
        "failed": failed_total,
    }


async def maybe_run_history_backfill(adapter: GeotabAdapter) -> dict[str, Any] | None:
    """Run the N-day backfill once per tenant (Redis NX flag).

    Returns a summary dict when the backfill ran, or ``None`` when skipped.
    """
    if not settings.HISTORY_BACKFILL_ON_STARTUP:
        logger.info("HISTORY_BACKFILL_ON_STARTUP=false — skipping history backfill")
        return None

    tenant_id = settings.GEOTAB_DATABASE
    if not tenant_id:
        logger.warning("GEOTAB_DATABASE unset — skipping history backfill")
        return None

    days = settings.HISTORY_BACKFILL_DAYS
    gps_days = settings.GPS_BACKFILL_DAYS
    key = bootstrap_key(tenant_id, days)
    redis = await get_redis()

    claimed = await redis.set(key, "running", nx=True)
    if not claimed:
        existing = await redis.get(key)
        logger.info("History backfill already claimed (%s=%s) — skipping", key, existing)
        return None

    if adapter.api is None:
        await adapter.connect()
    assert adapter.api is not None
    api = adapter.api

    try:
        logger.info(
            "Starting Geotab history backfill: hos_days=%d gps_days=%d tenant=%s",
            days,
            gps_days,
            tenant_id,
        )
        hos = await _backfill_hos(api, tenant_id, days)
        gps = await _backfill_gps(api, tenant_id, gps_days)
        await _advance_feed_tip(api, type_name="DutyStatusLog", provider="geotab", tenant_id=tenant_id)
        await _advance_feed_tip(
            api,
            type_name="LogRecord",
            provider=LOG_RECORD_PROVIDER,
            tenant_id=tenant_id,
        )
        await redis.set(key, "done")
        summary = {"hos": hos, "gps": gps, "days": days, "gps_days": gps_days}
        logger.info("History backfill complete: %s", summary)
        return summary
    except Exception:
        await redis.delete(key)
        logger.exception("History backfill failed — Redis flag cleared for retry")
        raise
