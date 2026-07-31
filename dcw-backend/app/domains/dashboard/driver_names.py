"""Resolve display names when Postgres rows lack ``driver_name``.

Live Geotab polling historically persisted null names; append-only logs cannot
be updated. Fall back to ``data/hos_30d_canonical.json`` (then 10d) and
``data/backtest_dispatches.json`` for the dashboard MVP.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("dcw.dashboard.driver_names")

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_CANDIDATES = (
    _BACKEND_ROOT / "data" / "hos_30d_canonical.json",
    _BACKEND_ROOT / "data" / "hos_10d_canonical.json",
)
_DISPATCHES_PATH = _BACKEND_ROOT / "data" / "backtest_dispatches.json"


@lru_cache(maxsize=1)
def load_driver_name_map() -> Dict[str, str]:
    names: Dict[str, str] = {}

    for canonical_path in _CANONICAL_CANDIDATES:
        if not canonical_path.exists():
            continue
        try:
            with canonical_path.open(encoding="utf-8") as fh:
                grouped = json.load(fh)
            for driver_id, records in grouped.items():
                for record in records:
                    name = record.get("driver_name")
                    if name:
                        names[str(driver_id)] = str(name)
                        break
            break
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            logger.warning("Failed loading names from %s: %s", canonical_path, exc)

    if _DISPATCHES_PATH.exists():
        try:
            with _DISPATCHES_PATH.open(encoding="utf-8") as fh:
                payload = json.load(fh)
            for row in payload.get("dispatches") or []:
                driver_id = row.get("driver_id")
                name = row.get("driver_name")
                if driver_id and name and str(driver_id) not in names:
                    names[str(driver_id)] = str(name)
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            logger.warning("Failed loading names from %s: %s", _DISPATCHES_PATH, exc)

    return names


def resolve_driver_name(driver_id: str, db_name: Optional[str] = None) -> Optional[str]:
    if db_name:
        return db_name
    return load_driver_name_map().get(driver_id)
