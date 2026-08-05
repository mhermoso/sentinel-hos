"""One-shot telematics history backfill for the last N days.

Geotab: GetFeed walks the entire retained feed from ``fromVersion=0``, which
can leave sparse recent coverage while the cursor sits at the tip. Date-range
``Get`` (DutyStatusLog + day-paged LogRecord) fills the lookback window, then
both feed cursors advance to the live tip.

Samsara: day-paged ``GET /fleet/hos/logs`` windows cover
``SAMSARA_HISTORY_BACKFILL_DAYS``, then the watermark cursor tips to now.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import mygeotab
import mygeotab.serializers as geo_serializers
from pydantic import ValidationError
from samsara.core.api_error import ApiError
from samsara.errors import TooManyRequestsError

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.redis import get_redis
from app.domains.dashboard.driver_names import save_driver_names_to_redis
from app.domains.ingestion.adapters.geotab import (
    GeotabAdapter,
    map_geotab_log_record_to_breadcrumb,
    map_geotab_log_to_canonical,
)
from app.domains.ingestion.adapters.samsara import (
    SamsaraAdapter,
    map_samsara_log_to_canonical,
)
from app.domains.ingestion.geotab_users import build_geotab_driver_name_map
from app.domains.ingestion.normalizer import normalize_batch
from app.domains.ingestion.repository import IngestionRepository
from app.domains.ingestion.schemas import DCWCanonicalHOSLog, DCWGpsBreadcrumb

logger = logging.getLogger("dcw.ingestion.history_backfill")

LOG_RECORD_PROVIDER = "geotab-logrecord"
_GEOTAB_GET_SOFT_CAP = 50_000


def bootstrap_key(tenant_id: str, days: int) -> str:
    """Redis flag: set when a successful N-day Geotab backfill has completed."""
    return f"bootstrap:geotab-history:{days}d:v1:{tenant_id}"


def samsara_bootstrap_key(fleet_id: str, days: int) -> str:
    """Redis flag: set when a successful N-day Samsara backfill has completed."""
    return f"bootstrap:samsara-history:{days}d:v1:{fleet_id}"


def _to_rfc3339(dt: datetime) -> str:
    """Serialize a datetime to an RFC 3339 UTC string (second precision)."""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plain_record(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return json.loads(geo_serializers.json_serialize(raw))


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

    driver_names = await asyncio.to_thread(build_geotab_driver_name_map, api)
    await save_driver_names_to_redis(tenant_id, driver_names)
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
        await IngestionRepository.update_active_drivers(tenant_id, driver_ids)
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


async def _fetch_samsara_hos_window(
    adapter: SamsaraAdapter,
    *,
    fleet_id: str,
    start_str: str,
    end_str: str,
) -> list[DCWCanonicalHOSLog]:
    """Paginate ``GET /fleet/hos/logs`` for one frozen ``startTime``/``endTime`` pair.

    Reuses the same SDK call + ``map_samsara_log_to_canonical`` path as live
    polling. Raises on rate-limit / API errors so the bootstrap Redis flag is
    cleared and the next startup retries.
    """
    if adapter.client is None:
        await adapter.connect()
    assert adapter.client is not None

    valid_logs: list[DCWCanonicalHOSLog] = []
    after: str | None = None
    page = 0

    while True:
        page += 1
        try:
            response = await adapter.client.hours_of_service.get_hos_logs(
                start_time=start_str,
                end_time=end_str,
                after=after,
            )
        except TooManyRequestsError:
            logger.error(
                "Samsara rate limit on backfill page %d (window %s→%s)",
                page,
                start_str,
                end_str,
            )
            raise
        except ApiError as exc:
            logger.error(
                "Samsara API error (status=%s) on backfill page %d (window %s→%s)",
                exc.status_code,
                page,
                start_str,
                end_str,
            )
            raise
        except httpx.HTTPError:
            logger.exception(
                "Samsara HTTP error on backfill page %d (window %s→%s)",
                page,
                start_str,
                end_str,
            )
            raise

        for group in response.data:
            driver_dict: dict[str, Any] = (
                group.driver.model_dump(by_alias=True, exclude_unset=True)
                if group.driver is not None
                else {}
            )
            for log_entry in group.hos_logs or []:
                entry_dict = log_entry.model_dump(by_alias=True, exclude_unset=True)
                try:
                    valid_logs.append(
                        map_samsara_log_to_canonical(fleet_id, driver_dict, entry_dict)
                    )
                except ValidationError as ve:
                    logger.warning(
                        "Validation failed for Samsara backfill entry "
                        "(driver=%s, logStartTime=%s): %s",
                        driver_dict.get("id"),
                        entry_dict.get("logStartTime"),
                        ve.errors(),
                    )
                except Exception as exc:
                    logger.warning(
                        "Unexpected Samsara backfill parse failure (driver=%s): %s",
                        driver_dict.get("id"),
                        exc,
                    )

        pagination = response.pagination
        if not pagination.has_next_page or not pagination.end_cursor:
            break
        after = pagination.end_cursor

    logger.info(
        "Samsara backfill window %s→%s: %d logs across %d page(s)",
        start_str,
        end_str,
        len(valid_logs),
        page,
    )
    if valid_logs:
        valid_logs = await adapter.enrich_logs_with_odometer(
            valid_logs,
            start_str=start_str,
            end_str=end_str,
        )
    return valid_logs


async def maybe_run_samsara_history_backfill(
    adapter: SamsaraAdapter,
) -> dict[str, Any] | None:
    """Run the N-day Samsara HOS backfill once per fleet (Redis NX flag).

    Fetches ``SAMSARA_HISTORY_BACKFILL_DAYS`` in 1-day windows via
    ``GET /fleet/hos/logs``, persists canonical rows, then tips
    ``cursor:samsara:{fleet_id}`` to now so the live poller continues from tip.

    Returns a summary dict when the backfill ran, or ``None`` when skipped.
    """
    fleet_id = adapter.fleet_id
    if not fleet_id:
        logger.warning("Samsara fleet_id unset — skipping history backfill")
        return None

    days = settings.SAMSARA_HISTORY_BACKFILL_DAYS
    key = samsara_bootstrap_key(fleet_id, days)
    redis = await get_redis()

    claimed = await redis.set(key, "running", nx=True)
    if not claimed:
        existing = await redis.get(key)
        logger.info("Samsara history backfill already claimed (%s=%s) — skipping", key, existing)
        return None

    try:
        now = datetime.now(UTC)
        window_start = now - timedelta(days=days)
        logger.info(
            "Starting Samsara history backfill: days=%d fleet=%s from=%s",
            days,
            fleet_id,
            _to_rfc3339(window_start),
        )

        fetched_total = 0
        inserted_total = 0
        driver_ids: set[str] = set()
        cursor = window_start

        while cursor < now:
            window_end = min(cursor + timedelta(days=1), now)
            # Freeze strings once per window — Samsara binds pagination to
            # the exact startTime/endTime parameter strings.
            start_str = _to_rfc3339(cursor)
            end_str = _to_rfc3339(window_end)
            raw_logs = await _fetch_samsara_hos_window(
                adapter,
                fleet_id=fleet_id,
                start_str=start_str,
                end_str=end_str,
            )
            fetched_total += len(raw_logs)

            if raw_logs:
                normalised = normalize_batch(raw_logs)
                async with async_session_factory() as session:
                    repo = IngestionRepository(session)
                    inserted = await repo.persist_canonical_logs(normalised)
                    await session.commit()
                inserted_total += inserted
                driver_ids.update(log.driver_id for log in normalised)

            cursor = window_end

        if driver_ids:
            await IngestionRepository.update_active_drivers(fleet_id, driver_ids)

        tip = _to_rfc3339(now)
        await IngestionRepository.save_cursor("samsara", fleet_id, tip)
        await redis.set(key, "done")

        summary = {
            "fetched": fetched_total,
            "inserted": inserted_total,
            "drivers": len(driver_ids),
            "days": days,
            "cursor": tip,
            "fleet_id": fleet_id,
        }
        logger.info("Samsara history backfill complete: %s", summary)
        return summary
    except Exception:
        await redis.delete(key)
        logger.exception("Samsara history backfill failed — Redis flag cleared for retry")
        raise
