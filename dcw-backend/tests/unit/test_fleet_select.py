"""Unit tests for dashboard fleet preference resolver."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from app.domains.dashboard import fleet_select as fleet_select_mod
from app.domains.dashboard import ui as ui_mod
from app.domains.dashboard.fleet_select import (
    COOKIE_NAME,
    default_fleet,
    resolve_fleet,
)
from app.domains.ingestion.fleets import Fleet

GEOTAB = Fleet(
    fleet_id="bbbBros",
    provider="geotab",
    display_name="bbbBros",
    enabled=True,
)
SAMSARA = Fleet(
    fleet_id="samsara:9005155",
    provider="samsara",
    display_name="Samsara",
    enabled=True,
)


def _request(cookies: dict[str, str] | None = None) -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/ui/home",
        "raw_path": b"/ui/home",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    if cookies:
        header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        scope["headers"] = [(b"cookie", header.encode())]
    return Request(scope)


def test_default_fleet_prefers_geotab() -> None:
    assert default_fleet([SAMSARA, GEOTAB]).fleet_id == "bbbBros"
    assert default_fleet([SAMSARA]).fleet_id == "samsara:9005155"


@pytest.mark.asyncio
async def test_resolve_fleet_query_over_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list() -> list[Fleet]:
        return [GEOTAB, SAMSARA]

    monkeypatch.setattr(fleet_select_mod, "list_enabled_fleets", fake_list)
    req = _request(cookies={COOKIE_NAME: GEOTAB.fleet_id})
    active = await resolve_fleet(req, fleet_param=SAMSARA.fleet_id)
    assert active.fleet_id == SAMSARA.fleet_id


@pytest.mark.asyncio
async def test_resolve_fleet_cookie_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list() -> list[Fleet]:
        return [GEOTAB, SAMSARA]

    monkeypatch.setattr(fleet_select_mod, "list_enabled_fleets", fake_list)
    req = _request(cookies={COOKIE_NAME: SAMSARA.fleet_id})
    active = await resolve_fleet(req)
    assert active.fleet_id == SAMSARA.fleet_id


@pytest.mark.asyncio
async def test_resolve_fleet_defaults_to_geotab(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list() -> list[Fleet]:
        return [GEOTAB, SAMSARA]

    monkeypatch.setattr(fleet_select_mod, "list_enabled_fleets", fake_list)
    active = await resolve_fleet(_request())
    assert active.fleet_id == GEOTAB.fleet_id


@pytest.mark.asyncio
async def test_resolve_fleet_rejects_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list() -> list[Fleet]:
        return [GEOTAB, SAMSARA]

    monkeypatch.setattr(fleet_select_mod, "list_enabled_fleets", fake_list)
    req = _request(cookies={COOKIE_NAME: "evil-tenant"})
    active = await resolve_fleet(req, fleet_param="not-a-fleet")
    assert active.fleet_id == GEOTAB.fleet_id


@pytest.mark.asyncio
async def test_base_context_hides_selector_for_single_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list() -> list[Fleet]:
        return [GEOTAB]

    monkeypatch.setattr(ui_mod, "list_enabled_fleets", fake_list)
    monkeypatch.setattr(fleet_select_mod, "list_enabled_fleets", fake_list)

    ctx = await ui_mod._base_context(_request())
    assert ctx["show_fleet_selector"] is False
    assert ctx["active_fleet"].fleet_id == GEOTAB.fleet_id
    assert len(ctx["fleets"]) == 1


@pytest.mark.asyncio
async def test_base_context_shows_selector_for_multiple_fleets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_list() -> list[Fleet]:
        return [GEOTAB, SAMSARA]

    monkeypatch.setattr(ui_mod, "list_enabled_fleets", fake_list)
    monkeypatch.setattr(fleet_select_mod, "list_enabled_fleets", fake_list)

    ctx = await ui_mod._base_context(_request())
    assert ctx["show_fleet_selector"] is True
    assert len(ctx["fleets"]) == 2
