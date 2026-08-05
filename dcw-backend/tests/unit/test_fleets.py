"""Unit tests for fleet registry bootstrap and listing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domains.ingestion import fleets as fleets_mod
from app.domains.ingestion.fleets import (
    Fleet,
    env_configured_fleets,
    list_enabled_fleets,
    sync_fleets_to_db,
)


def _clear_telematics_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleets_mod.settings, "GEOTAB_DATABASE", "")
    monkeypatch.setattr(fleets_mod.settings, "GEOTAB_USERNAME", "")
    monkeypatch.setattr(fleets_mod.settings, "GEOTAB_PASSWORD", "")
    monkeypatch.setattr(fleets_mod.settings, "SAMSARA_API_TOKEN", "")
    monkeypatch.setattr(fleets_mod.settings, "SAMSARA_FLEET_ID", "")


def test_env_configured_fleets_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_telematics_env(monkeypatch)
    assert env_configured_fleets() == []


def test_env_configured_fleets_geotab_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_telematics_env(monkeypatch)
    monkeypatch.setattr(fleets_mod.settings, "GEOTAB_DATABASE", "bbbBros")
    monkeypatch.setattr(fleets_mod.settings, "GEOTAB_USERNAME", "user")
    monkeypatch.setattr(fleets_mod.settings, "GEOTAB_PASSWORD", "pass")
    fleets = env_configured_fleets()
    assert len(fleets) == 1
    assert fleets[0].provider == "geotab"
    assert fleets[0].fleet_id == "bbbBros"


def test_env_configured_fleets_both(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_telematics_env(monkeypatch)
    monkeypatch.setattr(fleets_mod.settings, "GEOTAB_DATABASE", "bbbBros")
    monkeypatch.setattr(fleets_mod.settings, "GEOTAB_USERNAME", "user")
    monkeypatch.setattr(fleets_mod.settings, "GEOTAB_PASSWORD", "pass")
    monkeypatch.setattr(fleets_mod.settings, "SAMSARA_API_TOKEN", "tok")
    monkeypatch.setattr(fleets_mod.settings, "SAMSARA_FLEET_ID", "samsara:9005155")
    fleets = env_configured_fleets()
    assert len(fleets) == 2
    assert {f.provider for f in fleets} == {"geotab", "samsara"}
    samsara = next(f for f in fleets if f.provider == "samsara")
    assert samsara.fleet_id == "samsara:9005155"


def test_env_configured_fleets_samsara_default_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_telematics_env(monkeypatch)
    monkeypatch.setattr(fleets_mod.settings, "SAMSARA_API_TOKEN", "tok")
    fleets = env_configured_fleets()
    assert len(fleets) == 1
    assert fleets[0].fleet_id == "samsara:default"


class _SessionCM:
    def __init__(self, session: MagicMock) -> None:
        self._session = session

    async def __aenter__(self) -> MagicMock:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_sync_fleets_upsert_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_telematics_env(monkeypatch)
    monkeypatch.setattr(fleets_mod.settings, "SAMSARA_API_TOKEN", "tok")
    monkeypatch.setattr(fleets_mod.settings, "SAMSARA_FLEET_ID", "samsara:1")

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    def factory() -> _SessionCM:
        return _SessionCM(session)

    monkeypatch.setattr(fleets_mod, "async_session_factory", factory)

    first = await sync_fleets_to_db()
    second = await sync_fleets_to_db()
    assert first == second
    assert len(first) == 1
    assert session.execute.await_count == 2
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_list_enabled_fleets_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB results are returned as ordered by provider, fleet_id (SQL order_by)."""
    records = [
        MagicMock(
            fleet_id="bbbBros",
            provider="geotab",
            display_name="bbbBros",
            enabled=True,
        ),
        MagicMock(
            fleet_id="samsara:9005155",
            provider="samsara",
            display_name="Samsara",
            enabled=True,
        ),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = records

    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    def factory() -> _SessionCM:
        return _SessionCM(session)

    monkeypatch.setattr(fleets_mod, "async_session_factory", factory)

    fleets = await list_enabled_fleets()
    assert [f.provider for f in fleets] == ["geotab", "samsara"]
    assert [f.fleet_id for f in fleets] == ["bbbBros", "samsara:9005155"]
    assert all(isinstance(f, Fleet) for f in fleets)


@pytest.mark.asyncio
async def test_list_enabled_fleets_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_telematics_env(monkeypatch)
    monkeypatch.setattr(fleets_mod.settings, "SAMSARA_API_TOKEN", "tok")
    monkeypatch.setattr(fleets_mod.settings, "SAMSARA_FLEET_ID", "samsara:x")

    session = MagicMock()
    session.execute = AsyncMock(side_effect=RuntimeError("db down"))

    def factory() -> _SessionCM:
        return _SessionCM(session)

    monkeypatch.setattr(fleets_mod, "async_session_factory", factory)

    fleets = await list_enabled_fleets()
    assert len(fleets) == 1
    assert fleets[0].fleet_id == "samsara:x"
