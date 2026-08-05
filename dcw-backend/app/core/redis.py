"""Async Redis client for caching, pub/sub, and cursor management.

Provides connection lifecycle helpers and a thin pub/sub wrapper used
by the ingestion poller, compliance sweeper, and notifier subscriber.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger("dcw.redis")

# Module-level client — initialised via ``init_redis()``
_redis_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    """Create and return the global async Redis connection pool."""
    global _redis_client
    _redis_client = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=50,
    )
    # Quick connectivity check
    await _redis_client.ping()
    logger.info("Redis connection pool initialised (%s)", settings.REDIS_URL)
    return _redis_client


async def get_redis() -> aioredis.Redis:
    """Return the active Redis client (FastAPI dependency compatible)."""
    if _redis_client is None:
        return await init_redis()
    return _redis_client


async def close_redis() -> None:
    """Gracefully close the Redis connection pool."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection pool closed")


# ── Pub/Sub Helpers ──────────────────────────────────────────────────────

COMPLIANCE_ALERTS_CHANNEL = "compliance_alerts"


async def publish_event(channel: str, payload: str) -> int:
    """Publish a JSON-serialised event to a Redis pub/sub channel."""
    client = await get_redis()
    return await client.publish(channel, payload)


# ── Key Helpers ──────────────────────────────────────────────────────────

def cursor_key(provider: str, tenant_id: str) -> str:
    """Build the Redis key used to persist a provider polling cursor."""
    return f"cursor:{provider}:{tenant_id}"


def active_drivers_key(fleet_id: str) -> str:
    """Redis SET key holding driver IDs with recent activity for one fleet."""
    return f"set:active_drivers:{fleet_id}"


LEGACY_ACTIVE_DRIVERS_KEY = "set:active_drivers"


async def migrate_legacy_active_drivers(geotab_fleet_id: str) -> None:
    """One-time migration of the global active-drivers set to the Geotab fleet key.

    Before fleet namespacing, all active drivers lived in a single
    ``set:active_drivers`` key. Merge it into the per-fleet key and delete the
    legacy key; the exists-check makes this a run-once operation.
    """
    client = await get_redis()
    if not await client.exists(LEGACY_ACTIVE_DRIVERS_KEY):
        return
    target_key = active_drivers_key(geotab_fleet_id)
    merged = await client.sunionstore(  # type: ignore[misc]
        target_key, [target_key, LEGACY_ACTIVE_DRIVERS_KEY]
    )
    await client.delete(LEGACY_ACTIVE_DRIVERS_KEY)
    await client.expire(target_key, 86400)
    logger.info(
        "Migrated legacy %s into %s (%d driver(s))",
        LEGACY_ACTIVE_DRIVERS_KEY,
        target_key,
        merged,
    )


def alert_lock_key(
    tenant_id: str,
    driver_id: str,
    shift_id: str,
    rule: str,
    stage: str,
) -> str:
    """Build the idempotency lock key that prevents duplicate alerts."""
    return f"alert_lock:{tenant_id}:{driver_id}:{shift_id}:{rule}:{stage}"


def short_haul_fail_days_key(tenant_id: str, driver_id: str) -> str:
    """Redis SET of home-terminal ISO dates with short-haul exemption failures."""
    return f"short_haul_fail_days:{tenant_id}:{driver_id}"
