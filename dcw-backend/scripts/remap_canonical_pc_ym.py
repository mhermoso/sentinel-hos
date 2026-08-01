#!/usr/bin/env python3
"""Remap PC/YM statuses in an existing hos_30d_canonical.json in place.

Re-runs ``_map_geotab_status`` over each row's ``raw_payload`` and updates
``status`` + ``inputs_hash`` when the mapped status differs. Use when a live
Geotab re-fetch is unavailable.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.security import hash_canonical_log
from app.domains.ingestion.adapters.geotab import _map_geotab_status
from app.domains.ingestion.schemas import CanonicalDutyStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dcw.scripts.remap_canonical_pc_ym")


def remap_grouped(grouped: Dict[str, List[Dict[str, Any]]]) -> Tuple[int, int]:
    """Return (changed_count, scanned_count). Mutates ``grouped`` in place."""
    changed = 0
    scanned = 0
    for records in grouped.values():
        for record in records:
            scanned += 1
            raw = record.get("raw_payload") or {}
            if not isinstance(raw, dict):
                continue
            mapped = _map_geotab_status(raw.get("status"), raw.get("origin"))
            new_status = mapped.value
            old_status = record.get("status")
            if old_status == new_status:
                continue
            record["status"] = new_status
            record["inputs_hash"] = hash_canonical_log(record)
            changed += 1
            if mapped in (
                CanonicalDutyStatus.PERSONAL_CONVEYANCE,
                CanonicalDutyStatus.YARD_MOVE,
            ) or old_status in ("PC", "YM", "UNKNOWN"):
                logger.debug(
                    "raw_id=%s %s → %s (raw status=%s origin=%s)",
                    record.get("raw_id"),
                    old_status,
                    new_status,
                    raw.get("status"),
                    raw.get("origin"),
                )
    return changed, scanned


def main() -> None:
    parser = argparse.ArgumentParser(description="Remap PC/YM in hos_30d_canonical.json")
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=_ROOT / "data" / "hos_30d_canonical.json",
        help="Driver-grouped canonical JSON path",
    )
    args = parser.parse_args()

    if not args.path.exists():
        logger.error("File not found: %s", args.path)
        sys.exit(1)

    with args.path.open(encoding="utf-8") as fh:
        grouped = json.load(fh)

    if not isinstance(grouped, dict):
        logger.error("Expected driver-grouped object at %s", args.path)
        sys.exit(1)

    changed, scanned = remap_grouped(grouped)
    with args.path.open("w", encoding="utf-8") as fh:
        json.dump(grouped, fh, indent=2)
        fh.write("\n")

    # Summarize exemption counts after remap
    pc = ym = 0
    for records in grouped.values():
        for record in records:
            if record.get("status") == "PC":
                pc += 1
            elif record.get("status") == "YM":
                ym += 1

    logger.info(
        "Remapped %d / %d rows in %s (PC=%d, YM=%d)",
        changed,
        scanned,
        args.path,
        pc,
        ym,
    )


if __name__ == "__main__":
    main()
