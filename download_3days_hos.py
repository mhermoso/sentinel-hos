"""Script to fetch the last 3 days of DutyStatusLog data from MyGeotab
and save the canonical records to a local JSON file.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import sys
from typing import Any, Dict, List

import mygeotab
import mygeotab.serializers as geo_serializers
from geotab_ingestor import GeotabSettings, map_geotab_log_to_canonical

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("download_hos")


def main() -> None:
    # Load settings from environment variables or .env file
    settings = GeotabSettings()
    
    logger.info(f"Authenticating with MyGeotab server: {settings.geotab_server}, database: {settings.geotab_database}")
    api = mygeotab.API(
        username=settings.geotab_username,
        password=settings.geotab_password,
        database=settings.geotab_database,
        server=settings.geotab_server,
    )
    api.authenticate()
    logger.info("Successfully authenticated.")

    # Calculate timestamp for 3 days ago (UTC)
    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=3)
    from_date_iso = from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    logger.info("Fetching all Users to build driver name lookup...")
    users = api.get("User")
    # Build { driver_id -> "First Last" } map
    driver_name_map: Dict[str, str] = {}
    for u in users:
        uid = u.get("id")
        if uid:
            first = u.get("firstName", "") or ""
            last = u.get("lastName", "") or ""
            full_name = f"{first} {last}".strip() or u.get("name", uid)
            driver_name_map[str(uid)] = full_name
    logger.info(f"Loaded {len(driver_name_map)} driver name entries.")

    logger.info(f"Fetching DutyStatusLog records from {from_date_iso} to now...")

    # Query MyGeotab for DutyStatusLog starting from 3 days ago
    logs = api.get(
        "DutyStatusLog",
        search={
            "fromDate": from_date_iso,
        },
    )

    logger.info(f"Retrieved {len(logs)} raw DutyStatusLog records from MyGeotab.")

    canonical_records: List[Dict[str, Any]] = []
    failed_count = 0

    for log_dict in logs:
        try:
            # Normalize SDK objects (datetime, Entity) to plain Python dicts via JSON round-trip
            log_plain = json.loads(geo_serializers.json_serialize(log_dict))
            driver_id = str(((log_plain.get("driver") or {}) if isinstance(log_plain.get("driver"), dict) else {}).get("id", "UNKNOWN_DRIVER"))
            resolved_name = driver_name_map.get(driver_id)
            canonical_obj = map_geotab_log_to_canonical(
                log_plain,
                tenant_id=settings.geotab_database,
                driver_name=resolved_name,
            )
            canonical_records.append(canonical_obj.model_dump(mode="json"))
        except Exception as exc:
            failed_count += 1
            logger.warning(f"Failed to map record {log_dict.get('id')}: {exc}")

    output_filename = f"hos_logs_last_3_days_{now.strftime('%Y%m%d_%H%M%S')}.json"
    output_path = os.path.join(os.getcwd(), output_filename)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(canonical_records, f, indent=2)

    logger.info(f"Successfully processed {len(canonical_records)} records ({failed_count} failed).")
    logger.info(f"Saved dataset to: {output_path}")


if __name__ == "__main__":
    main()
