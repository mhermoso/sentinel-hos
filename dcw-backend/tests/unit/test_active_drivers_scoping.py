"""Unit tests for per-fleet active_drivers Redis keys + legacy migration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import redis as redis_mod
from app.core.redis import (
    LEGACY_ACTIVE_DRIVERS_KEY,
    active_drivers_key,
    migrate_legacy_active_drivers,
)
from app.domains.ingestion.repository import IngestionRepository


def test_active_drivers_key_is_fleet_scoped() -> None:
    assert active_drivers_key("bbbBros") == "set:active_drivers:bbbBros"
    assert active_drivers_key("samsara:9005155") == "set:active_drivers:samsara:9005155"
    assert active_drivers_key("bbbBros") != active_drivers_key("samsara:9005155")


@pytest.mark.asyncio
async def test_update_active_drivers_isolates_fleets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store: dict[str, set[str]] = {}

    fake_redis = MagicMock()

    async def sadd(key: str, *members: str) -> int:
        store.setdefault(key, set()).update(members)
        return len(members)

    async def smembers(key: str) -> set[str]:
        return set(store.get(key, set()))

    async def expire(key: str, ttl: int) -> bool:
        return True

    fake_redis.sadd = AsyncMock(side_effect=sadd)
    fake_redis.smembers = AsyncMock(side_effect=smembers)
    fake_redis.expire = AsyncMock(side_effect=expire)

    async def fake_get_redis() -> MagicMock:
        return fake_redis

    monkeypatch.setattr(
        "app.domains.ingestion.repository.get_redis",
        fake_get_redis,
    )

    await IngestionRepository.update_active_drivers("fleetA", {"d1", "d2"})
    await IngestionRepository.update_active_drivers("fleetB", {"d3"})

    assert await IngestionRepository.get_active_driver_ids("fleetA") == {"d1", "d2"}
    assert await IngestionRepository.get_active_driver_ids("fleetB") == {"d3"}
    assert "set:active_drivers:fleetA" in store
    assert "set:active_drivers:fleetB" in store


@pytest.mark.asyncio
async def test_migrate_legacy_active_drivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = MagicMock()
    fake_redis.exists = AsyncMock(return_value=1)
    fake_redis.sunionstore = AsyncMock(return_value=3)
    fake_redis.delete = AsyncMock(return_value=1)
    fake_redis.expire = AsyncMock(return_value=True)

    async def fake_get_redis() -> MagicMock:
        return fake_redis

    monkeypatch.setattr(redis_mod, "get_redis", fake_get_redis)

    await migrate_legacy_active_drivers("bbbBros")

    fake_redis.exists.assert_awaited_once_with(LEGACY_ACTIVE_DRIVERS_KEY)
    fake_redis.sunionstore.assert_awaited_once_with(
        "set:active_drivers:bbbBros",
        ["set:active_drivers:bbbBros", LEGACY_ACTIVE_DRIVERS_KEY],
    )
    fake_redis.delete.assert_awaited_once_with(LEGACY_ACTIVE_DRIVERS_KEY)
    fake_redis.expire.assert_awaited_once_with("set:active_drivers:bbbBros", 86400)


@pytest.mark.asyncio
async def test_migrate_legacy_noop_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = MagicMock()
    fake_redis.exists = AsyncMock(return_value=0)
    fake_redis.sunionstore = AsyncMock()
    fake_redis.delete = AsyncMock()

    async def fake_get_redis() -> MagicMock:
        return fake_redis

    monkeypatch.setattr(redis_mod, "get_redis", fake_get_redis)

    await migrate_legacy_active_drivers("bbbBros")

    fake_redis.sunionstore.assert_not_awaited()
    fake_redis.delete.assert_not_awaited()
