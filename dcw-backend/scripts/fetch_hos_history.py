#!/usr/bin/env python3
"""Fetch historical Geotab HOS logs and write canonical JSON grouped by driver.

Uses production adapter mapping + normalizer from ``app.domains.ingestion``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

# Allow running as ``python scripts/fetch_hos_history.py`` from dcw-backend/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import mygeotab
import mygeotab.serializers as geo_serializers

from app.core.config import settings
from app.core.database import async_session_factory
from app.domains.ingestion.adapters.geotab import map_geotab_log_to_canonical
from app.domains.ingestion.normalizer import normalize_batch
from app.domains.ingestion.repository import IngestionRepository
from app.domains.ingestion.schemas import DCWCanonicalHOSLog

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dcw.scripts.fetch_hos_history")


def _build_driver_name_map(api: mygeotab.API) -> Dict[str, str]:
    users = api.get("User")
    name_map: Dict[str, str] = {}
    for user in users:
        uid = user.get("id")
        if not uid:
            continue
        first = user.get("firstName", "") or ""
        last = user.get("lastName", "") or ""
        full_name = f"{first} {last}".strip() or user.get("name", uid)
        name_map[str(uid)] = full_name
    return name_map


def fetch_geotab_logs(days: int, tenant_id: str) -> List[DCWCanonicalHOSLog]:
    """Fetch DutyStatusLog records for the last ``days`` via MyGeotab Get API."""
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

    driver_names = _build_driver_name_map(api)
    logger.info("Loaded %d driver name entries.", len(driver_names))

    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=days)
    from_date_iso = from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    logger.info("Fetching DutyStatusLog from %s …", from_date_iso)

    raw_logs = api.get("DutyStatusLog", search={"fromDate": from_date_iso})
    logger.info("Retrieved %d raw DutyStatusLog records.", len(raw_logs))

    canonical: List[DCWCanonicalHOSLog] = []
    failed = 0
    for raw in raw_logs:
        try:
            plain = json.loads(geo_serializers.json_serialize(raw))
            driver_id = str(
                ((plain.get("driver") or {}) if isinstance(plain.get("driver"), dict) else {}).get(
                    "id", "UNKNOWN_DRIVER"
                )
            )
            log = map_geotab_log_to_canonical(
                plain,
                tenant_id=tenant_id,
                driver_name=driver_names.get(driver_id),
            )
            canonical.append(log)
        except Exception as exc:
            failed += 1
            logger.warning("Failed to map record %s: %s", raw.get("id"), exc)

    normalized = normalize_batch(canonical)
    logger.info("Mapped %d records (%d failed), normalized batch.", len(normalized), failed)
    return normalized


def group_by_driver(logs: List[DCWCanonicalHOSLog]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for log in logs:
        grouped[log.driver_id].append(log.model_dump(mode="json"))
    for driver_id in grouped:
        grouped[driver_id].sort(key=lambda r: r["event_timestamp"])
    return dict(grouped)


async def persist_logs(logs: List[DCWCanonicalHOSLog]) -> int:
    async with async_session_factory() as session:
        repo = IngestionRepository(session)
        inserted = await repo.persist_canonical_logs(logs)
        driver_ids = {log.driver_id for log in logs}
        await IngestionRepository.update_active_drivers(driver_ids)
        await session.commit()
        return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Geotab HOS history as canonical JSON")
    parser.add_argument("--days", type=int, default=10, help="Lookback window in days (default: 10)")
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "data" / "hos_10d_canonical.json",
        help="Output JSON path (grouped by driver_id)",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Insert canonical logs into PostgreSQL via IngestionRepository",
    )
    parser.add_argument(
        "--tenant-id",
        default=settings.GEOTAB_DATABASE,
        help="Tenant identifier (defaults to GEOTAB_DATABASE)",
    )
    args = parser.parse_args()

    tenant_id = args.tenant_id or settings.GEOTAB_DATABASE
    if not tenant_id:
        logger.error("Tenant ID required — set GEOTAB_DATABASE or pass --tenant-id")
        sys.exit(1)

    logs = fetch_geotab_logs(days=args.days, tenant_id=tenant_id)
    grouped = group_by_driver(logs)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        json.dump(grouped, fh, indent=2)

    total_events = sum(len(v) for v in grouped.values())
    logger.info(
        "Wrote %d drivers / %d events to %s",
        len(grouped),
        total_events,
        args.output,
    )

    if args.persist:
        inserted = asyncio.run(persist_logs(logs))
        logger.info("Persisted %d new rows to PostgreSQL.", inserted)


if __name__ == "__main__":
    main()
