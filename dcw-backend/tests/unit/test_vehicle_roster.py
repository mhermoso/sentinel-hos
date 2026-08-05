"""Unit tests for vehicle_roster upsert + unit enrichment helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domains.dashboard.units import resolve_current_driver_id
from app.domains.ingestion.schemas import DriverRosterEntry, VehicleRosterEntry
from app.domains.ingestion.vehicle_roster_repository import VehicleRosterRepository


def _vehicle(
    device_id: str = "dev-1",
    *,
    current_driver_id: str | None = "b382",
) -> VehicleRosterEntry:
    return VehicleRosterEntry(
        provider="geotab",
        tenant_id="fleet1",
        external_device_id=device_id,
        name="Unit 12",
        vin="VIN123",
        current_driver_id=current_driver_id,
    )


def _driver(driver_id: str, device_id: str | None) -> DriverRosterEntry:
    return DriverRosterEntry(
        provider="geotab",
        tenant_id="fleet1",
        external_driver_id=driver_id,
        display_name=driver_id,
        current_device_id=device_id,
        has_unit_assignment=bool(device_id),
    )


def test_resolve_current_driver_prefers_vehicle_cache() -> None:
    vehicle = _vehicle(current_driver_id="cached")
    assignees = [_driver("a1", "dev-1"), _driver("a2", "dev-1")]
    assert resolve_current_driver_id(vehicle, assignees, "hos-last") == "cached"


def test_resolve_current_driver_single_assignee() -> None:
    vehicle = _vehicle(current_driver_id=None)
    assignees = [_driver("solo", "dev-1")]
    assert resolve_current_driver_id(vehicle, assignees, "hos-last") == "solo"


def test_resolve_current_driver_falls_back_to_hos() -> None:
    vehicle = _vehicle(current_driver_id=None)
    assignees = [_driver("a1", "dev-1"), _driver("a2", "dev-1")]
    assert resolve_current_driver_id(vehicle, assignees, "hos-last") == "hos-last"


def test_resolve_current_driver_ignores_unassigned_sentinel() -> None:
    vehicle = _vehicle(current_driver_id="unassigned:device:dev-1")
    assignees: list[DriverRosterEntry] = []
    assert resolve_current_driver_id(vehicle, assignees, "real-driver") == "real-driver"


@pytest.mark.asyncio
async def test_vehicle_roster_upsert_entries() -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()

    repo = VehicleRosterRepository(session)
    count = await repo.upsert_entries([_vehicle(), _vehicle("dev-2", current_driver_id=None)])
    assert count == 2
    assert session.execute.await_count == 2
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_vehicle_roster_upsert_empty() -> None:
    session = MagicMock()
    repo = VehicleRosterRepository(session)
    assert await repo.upsert_entries([]) == 0
