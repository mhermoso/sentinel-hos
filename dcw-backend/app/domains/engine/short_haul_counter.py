"""Redis persistence for rolling short-haul exemption-failure days (8-in-30).

Key: ``short_haul_fail_days:{tenant_id}:{driver_id}`` — Redis SET of ISO dates
(home-terminal calendar days). Members older than 30 days are pruned on read/write.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Iterable, Set
from zoneinfo import ZoneInfo

from app.core.redis import get_redis, short_haul_fail_days_key
from app.domains.engine.short_haul import home_terminal_day

logger = logging.getLogger("dcw.engine.short_haul_counter")

WINDOW_DAYS: int = 30


def _prune_dates(dates: Iterable[str], as_of_day: date) -> Set[str]:
    cutoff = as_of_day - timedelta(days=WINDOW_DAYS - 1)
    kept: Set[str] = set()
    for raw in dates:
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            continue
        if d >= cutoff:
            kept.add(raw)
    return kept


async def get_short_haul_failure_days(
    tenant_id: str,
    driver_id: str,
    *,
    as_of: datetime,
    home_terminal_timezone: str,
) -> Set[str]:
    """Return ISO failure days within the rolling 30-day window."""
    redis = await get_redis()
    key = short_haul_fail_days_key(tenant_id, driver_id)
    members = await redis.smembers(key)
    as_of_day = date.fromisoformat(home_terminal_day(as_of, home_terminal_timezone))
    kept = _prune_dates(members, as_of_day)
    stale = set(members) - kept
    if stale:
        await redis.srem(key, *stale)
    return kept


async def count_short_haul_failures_30(
    tenant_id: str,
    driver_id: str,
    *,
    as_of: datetime,
    home_terminal_timezone: str,
) -> int:
    """Count exemption-failure days in the rolling 30-day window."""
    days = await get_short_haul_failure_days(
        tenant_id,
        driver_id,
        as_of=as_of,
        home_terminal_timezone=home_terminal_timezone,
    )
    return len(days)


async def record_short_haul_failure_day(
    tenant_id: str,
    driver_id: str,
    *,
    as_of: datetime,
    home_terminal_timezone: str,
) -> int:
    """Mark today's home-terminal day as an exemption failure; return new count."""
    redis = await get_redis()
    key = short_haul_fail_days_key(tenant_id, driver_id)
    day = home_terminal_day(as_of, home_terminal_timezone)
    await redis.sadd(key, day)
    # Expire key after ~60 days of inactivity (safety net; pruning is authoritative).
    await redis.expire(key, 60 * 86400)
    days = await get_short_haul_failure_days(
        tenant_id,
        driver_id,
        as_of=as_of,
        home_terminal_timezone=home_terminal_timezone,
    )
    logger.info(
        "Recorded short-haul failure day tenant=%s driver=%s day=%s count_30=%d",
        tenant_id,
        driver_id,
        day,
        len(days),
    )
    return len(days)


def local_as_of_day(as_of: datetime, home_terminal_timezone: str) -> date:
    """Home-terminal calendar date for ``as_of``."""
    tz = ZoneInfo(home_terminal_timezone)
    local = as_of.astimezone(tz) if as_of.tzinfo else as_of.replace(tzinfo=tz)
    return local.date()
