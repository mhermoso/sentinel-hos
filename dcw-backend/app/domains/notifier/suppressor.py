"""Alert lock suppressor — prevents duplicate Twilio calls per shift/rule/stage.

Implements the idempotency layer described in the architecture spec:
  - Key: ``alert_lock:{tenant}:{driver}:{shift}:{rule}:{stage}``
  - TTL: 12 hours (one shift window)
  - 1 call per stage per shift per rule guaranteed.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.redis import alert_lock_key, get_redis

logger = logging.getLogger("dcw.notifier.suppressor")

# Alert lock TTL — 12 hours covers one full duty shift
ALERT_LOCK_TTL_SECONDS: int = 12 * 3600


async def should_suppress_alert(
    tenant_id: str,
    driver_id: str,
    shift_id: str,
    rule: str,
    stage: str,
) -> tuple[bool, Optional[str]]:
    """Check whether an alert should be suppressed.

    Returns:
        (suppressed: bool, reason: Optional[str])
        If suppressed is True, the alert should NOT be dispatched.
    """
    redis = await get_redis()
    key = alert_lock_key(tenant_id, driver_id, shift_id, rule, stage)

    existing = await redis.get(key)
    if existing:
        reason = f"Alert lock active for key: {key}"
        logger.info("Suppressing duplicate alert: %s", reason)
        return True, reason

    return False, None


async def acquire_alert_lock(
    tenant_id: str,
    driver_id: str,
    shift_id: str,
    rule: str,
    stage: str,
) -> bool:
    """Acquire the alert lock (SET NX) to prevent future duplicates.

    Returns True if the lock was acquired, False if already locked.
    """
    redis = await get_redis()
    key = alert_lock_key(tenant_id, driver_id, shift_id, rule, stage)

    # SET key "1" EX ttl NX — atomic acquire
    acquired = await redis.set(key, "1", ex=ALERT_LOCK_TTL_SECONDS, nx=True)
    if acquired:
        logger.debug("Alert lock acquired: %s (TTL=%dh)", key, ALERT_LOCK_TTL_SECONDS // 3600)
    return bool(acquired)


async def release_alert_lock(
    tenant_id: str,
    driver_id: str,
    shift_id: str,
    rule: str,
    stage: str,
) -> None:
    """Release an alert lock (used for testing / manual reset)."""
    redis = await get_redis()
    key = alert_lock_key(tenant_id, driver_id, shift_id, rule, stage)
    await redis.delete(key)
    logger.info("Alert lock released: %s", key)
