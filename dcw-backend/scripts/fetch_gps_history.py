#!/usr/bin/env python3
"""Fetch historical Geotab LogRecord GPS breadcrumbs and persist (ADR-007).

Uses production adapter mapping + device→driver attribution from
``app.domains.ingestion``. Past days get route trails after deploy via::

    python scripts/fetch_gps_history.py --days 7 --persist
    python scripts/fetch_gps_history.py --advance-cursor
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Allow running as ``python scripts/fetch_gps_history.py`` from dcw-backend/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import mygeotab
import mygeotab.serializers as geo_serializers

from app.core.config import settings
from app.core.database import async_session_factory, init_db
from app.domains.ingestion.adapters.geotab import map_geotab_log_record_to_breadcrumb
from app.domains.ingestion.repository import IngestionRepository
from app.domains.ingestion.schemas import DCWGpsBreadcrumb

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dcw.scripts.fetch_gps_history")

LOG_RECORD_PROVIDER = "geotab-logrecord"
# Geotab Get() silently caps large result sets; page by day to stay under the limit.
_GEOTAB_GET_SOFT_CAP = 50_000


def _plain_record(raw: Any) -> dict[str, Any]:
    return json.loads(geo_serializers.json_serialize(raw))


def _authenticate() -> mygeotab.API:
    logger.info(
        "Authenticating with MyGeotab (server=%s, database=%s)",
        settings.GEOTAB_SERVER,
        settings.GEOTAB_DATABASE,
    )
    api = mygeotab.API(
        username=settings.GEOTAB_USERNAME,
        password=settings.GEOTAB_PASSWORD,
        database=settings.GEOTAB_DATABASE,
        server=settings.GEOTAB_SERVER,
    )
    api.authenticate()
    logger.info("Authenticated successfully.")
    return api


def _fetch_log_records_window(
    api: mygeotab.API,
    from_dt: datetime,
    to_dt: datetime,
) -> list[Any]:
    """Fetch LogRecords in [from_dt, to_dt), subdividing if Geotab hits the soft cap."""
    from_iso = from_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to_iso = to_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    search: dict[str, str] = {"fromDate": from_iso, "toDate": to_iso}
    raw_logs = api.get("LogRecord", search=search)
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


async def _map_raw_logs(
    raw_logs: list[Any],
    tenant_id: str,
    device_driver_cache: dict[str, str],
) -> tuple[list[DCWGpsBreadcrumb], int]:
    breadcrumbs: list[DCWGpsBreadcrumb] = []
    failed = 0
    async with async_session_factory() as session:
        repo = IngestionRepository(session)
        for raw in raw_logs:
            plain: dict[str, Any] = {}
            try:
                plain = _plain_record(raw) if not isinstance(raw, dict) else raw
                device = plain.get("device")
                if isinstance(device, dict):
                    device_id = str(device.get("id") or "")
                elif isinstance(device, str):
                    device_id = device
                else:
                    device_id = ""
                if not device_id:
                    failed += 1
                    continue

                event_ts = plain.get("dateTime")
                if isinstance(event_ts, str):
                    event_ts = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
                if isinstance(event_ts, datetime) and event_ts.tzinfo is None:
                    event_ts = event_ts.replace(tzinfo=UTC)
                if not isinstance(event_ts, datetime):
                    failed += 1
                    continue

                driver_id = await repo.resolve_driver_for_device(
                    tenant_id=tenant_id,
                    device_id=device_id,
                    as_of=event_ts,
                    cache=device_driver_cache,
                )
                crumb = map_geotab_log_record_to_breadcrumb(
                    plain, tenant_id=tenant_id, driver_id=driver_id
                )
                breadcrumbs.append(crumb)
            except Exception as exc:
                failed += 1
                logger.warning("Failed to map LogRecord %s: %s", plain.get("id"), exc)
    return breadcrumbs, failed


async def fetch_and_map_log_records(
    days: int,
    tenant_id: str,
) -> list[DCWGpsBreadcrumb]:
    """Fetch LogRecord by date range (day-paged) and map with device→driver attribution."""
    api = _authenticate()

    now = datetime.now(UTC)
    from_date = now - timedelta(days=days)
    logger.info("Fetching LogRecord from %s … (day-paged)", from_date.isoformat())

    await init_db()
    breadcrumbs: list[DCWGpsBreadcrumb] = []
    failed_total = 0
    device_driver_cache: dict[str, str] = {}

    # Page day-by-day so a single Get() stays under Geotab's ~50k soft cap.
    cursor = from_date
    while cursor < now:
        window_end = min(cursor + timedelta(days=1), now)
        raw_logs = await asyncio.to_thread(_fetch_log_records_window, api, cursor, window_end)
        mapped, failed = await _map_raw_logs(raw_logs, tenant_id, device_driver_cache)
        breadcrumbs.extend(mapped)
        failed_total += failed
        cursor = window_end

    logger.info(
        "Mapped %d breadcrumbs (%d failed) across %d day window(s).",
        len(breadcrumbs),
        failed_total,
        days,
    )
    return breadcrumbs


async def persist_breadcrumbs(crumbs: list[DCWGpsBreadcrumb]) -> int:
    await init_db()
    async with async_session_factory() as session:
        repo = IngestionRepository(session)
        inserted = await repo.persist_gps_breadcrumbs(crumbs)
        await session.commit()
        return inserted


async def _run_fetch_persist(
    days: int,
    tenant_id: str,
    output: Path,
    persist: bool,
) -> None:
    crumbs = await fetch_and_map_log_records(days=days, tenant_id=tenant_id)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        json.dump([c.model_dump(mode="json") for c in crumbs], fh, indent=2)
    logger.info("Wrote %d breadcrumbs to %s", len(crumbs), output)

    if persist:
        inserted = await persist_breadcrumbs(crumbs)
        logger.info("Persisted %d new rows to PostgreSQL.", inserted)


async def advance_log_record_cursor(tenant_id: str) -> str:
    """Advance Redis LogRecord cursor to the current Geotab GetFeed tip.

    Omitting ``fromVersion`` returns an empty batch and a ``toVersion`` at the
    live tip (Geotab Data Feed guide — "start from now"), so the ARQ poller
    stops replaying mid-history after a date-range backfill.
    """
    api = _authenticate()

    previous = await IngestionRepository.load_cursor(LOG_RECORD_PROVIDER, tenant_id)
    feed_response = await asyncio.to_thread(api.call, "GetFeed", typeName="LogRecord")
    tip = str(feed_response.get("toVersion", ""))
    if not tip:
        logger.error("GetFeed returned no toVersion — cursor not advanced")
        sys.exit(1)

    result = feed_response.get("result", feed_response.get("data", [])) or []
    await IngestionRepository.save_cursor(LOG_RECORD_PROVIDER, tenant_id, tip)
    logger.info(
        "Advanced cursor:geotab-logrecord:%s  previous=%s  tip=%s  (feed rows=%d)",
        tenant_id,
        previous,
        tip,
        len(result),
    )
    return tip


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Geotab LogRecord GPS history into gps_breadcrumbs"
    )
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days (default: 7)")
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "data" / "gps_breadcrumbs.json",
        help="Optional JSON dump path (list of breadcrumbs)",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Insert breadcrumbs into PostgreSQL via IngestionRepository",
    )
    parser.add_argument(
        "--advance-cursor",
        action="store_true",
        help=(
            "Set Redis cursor:geotab-logrecord:{tenant} to the current GetFeed tip "
            "(omit fromVersion). Can be used alone or after a --persist backfill."
        ),
    )
    parser.add_argument(
        "--tenant-id",
        default=settings.GEOTAB_DATABASE,
        help="Tenant identifier (defaults to GEOTAB_DATABASE)",
    )
    args = parser.parse_args()

    tenant_id: str | None = args.tenant_id or settings.GEOTAB_DATABASE
    if not tenant_id:
        logger.error("Tenant ID required — set GEOTAB_DATABASE or pass --tenant-id")
        sys.exit(1)

    # One-shot: python scripts/fetch_gps_history.py --advance-cursor
    if args.advance_cursor and not args.persist and "--days" not in sys.argv:
        asyncio.run(advance_log_record_cursor(tenant_id))
        return

    async def _main() -> None:
        await _run_fetch_persist(
            days=args.days,
            tenant_id=tenant_id,
            output=args.output,
            persist=args.persist,
        )
        if args.advance_cursor:
            await advance_log_record_cursor(tenant_id)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
