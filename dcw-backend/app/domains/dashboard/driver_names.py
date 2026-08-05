"""Resolve display names when Postgres rows lack ``driver_name``.

Live Geotab GetFeed historically persisted null names; append-only
``canonical_hos_logs`` cannot be updated. Resolution order:

1. Non-empty ``db_name`` from Postgres
2. In-process cache (warmed from Redis / Geotab User / seed JSON)
3. Seed JSON fallback (``data/hos_*_canonical.json``, backtest dispatches)

The ARQ worker (and API cold-start) refresh Redis hash
``hash:driver_names:{tenant}`` from MyGeotab ``User``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.redis import get_redis
from app.domains.ingestion.geotab_users import build_geotab_driver_name_map

logger = logging.getLogger("dcw.dashboard.driver_names")

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_CANDIDATES = (
    _BACKEND_ROOT / "data" / "hos_30d_canonical.json",
    _BACKEND_ROOT / "data" / "hos_10d_canonical.json",
)
_DISPATCHES_PATH = _BACKEND_ROOT / "data" / "backtest_dispatches.json"

# Process-local map used by sync ``resolve_driver_name`` (API request path).
_RUNTIME_NAMES: dict[str, str] = {}


def driver_names_key(tenant_id: str) -> str:
    """Redis HASH key for Geotab user id → display name."""
    return f"hash:driver_names:{tenant_id}"


async def save_driver_names_to_redis(tenant_id: str, names: dict[str, str]) -> int:
    """Replace the tenant driver-name hash in Redis and warm the process cache."""
    global _RUNTIME_NAMES
    if not tenant_id or not names:
        return 0
    redis = await get_redis()
    key = driver_names_key(tenant_id)
    pipe = redis.pipeline()
    pipe.delete(key)
    pipe.hset(key, mapping=names)
    await pipe.execute()
    _RUNTIME_NAMES = {**load_driver_name_map(), **names}
    logger.info("Cached %d driver names in Redis (%s)", len(names), key)
    return len(names)


async def load_driver_names_from_redis(tenant_id: str) -> dict[str, str]:
    """Load the tenant driver-name hash from Redis (empty if missing)."""
    if not tenant_id:
        return {}
    redis = await get_redis()
    raw = await redis.hgetall(driver_names_key(tenant_id))
    return {str(k): str(v) for k, v in raw.items() if v}


async def refresh_driver_names_from_geotab(api: mygeotab.API, tenant_id: str) -> int:
    """Pull User names from Geotab, persist to Redis, warm process cache."""
    names = await asyncio.to_thread(build_geotab_driver_name_map, api)
    return await save_driver_names_to_redis(tenant_id, names)


async def warm_driver_name_cache(*, geotab_api: Any | None = None) -> int:
    """Warm process cache from Redis; optionally refresh from Geotab if empty.

    Called on API and worker startup so dashboard resolves names even when
    append-only HOS rows still have ``driver_name IS NULL``.
    """
    global _RUNTIME_NAMES
    # Geotab-only: Samsara persists driver_name on canonical rows; do not merge
    # Samsara names into the process-global _RUNTIME_NAMES cache.
    tenant_id = settings.GEOTAB_DATABASE or ""
    seed = load_driver_name_map()
    redis_names = await load_driver_names_from_redis(tenant_id)

    if not redis_names and geotab_api is not None and tenant_id:
        try:
            count = await refresh_driver_names_from_geotab(geotab_api, tenant_id)
            return count
        except Exception as exc:
            logger.warning("Geotab driver-name refresh failed: %s", exc)

    _RUNTIME_NAMES = {**seed, **redis_names}
    logger.info(
        "Warmed driver name cache: %d names (redis=%d seed=%d)",
        len(_RUNTIME_NAMES),
        len(redis_names),
        len(seed),
    )
    return len(_RUNTIME_NAMES)


@lru_cache(maxsize=1)
def load_driver_name_map() -> dict[str, str]:
    """Seed-file fallback for local/dev when Redis/Geotab are unavailable."""
    names: dict[str, str] = {}

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


def resolve_driver_name(driver_id: str, db_name: str | None = None) -> str | None:
    """Return a display name for ``driver_id`` (db → runtime cache → seed JSON)."""
    if db_name:
        return db_name
    if driver_id in _RUNTIME_NAMES:
        return _RUNTIME_NAMES[driver_id]
    return load_driver_name_map().get(driver_id)
