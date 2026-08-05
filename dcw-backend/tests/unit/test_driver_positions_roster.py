"""Unit tests for Home map position enrichment from driver roster flags."""

from __future__ import annotations

from app.domains.dashboard.router import _position_roster_fields
from app.domains.ingestion.schemas import DriverRosterEntry


def _roster(
    external_driver_id: str,
    *,
    has_unit_assignment: bool = False,
    unit_label: str | None = None,
) -> DriverRosterEntry:
    return DriverRosterEntry(
        provider="geotab",
        tenant_id="fleet1",
        external_driver_id=external_driver_id,
        display_name="Test Driver",
        has_unit_assignment=has_unit_assignment,
        unit_label=unit_label,
        current_device_id="dev-1" if has_unit_assignment else None,
    )


def test_position_roster_fields_on_unit() -> None:
    roster_by_id = {
        "b382": _roster("b382", has_unit_assignment=True, unit_label="Unit 12"),
    }
    has_unit, label = _position_roster_fields("b382", roster_by_id)
    assert has_unit is True
    assert label == "Unit 12"


def test_position_roster_fields_off_unit() -> None:
    roster_by_id = {
        "b382": _roster("b382", has_unit_assignment=False, unit_label=None),
    }
    has_unit, label = _position_roster_fields("b382", roster_by_id)
    assert has_unit is False
    assert label is None


def test_position_roster_fields_missing_roster() -> None:
    has_unit, label = _position_roster_fields("unknown-driver", {})
    assert has_unit is None
    assert label is None


def test_position_roster_fields_unassigned_sentinel() -> None:
    # Even if a stray roster key existed, unassigned HOS ids are never on-unit.
    roster_by_id = {
        "unassigned:device:truck1": _roster(
            "unassigned:device:truck1",
            has_unit_assignment=True,
            unit_label="Truck 1",
        ),
    }
    has_unit, label = _position_roster_fields("unassigned:device:truck1", roster_by_id)
    assert has_unit is False
    assert label is None


def test_position_roster_fields_unknown_driver_sentinel() -> None:
    has_unit, label = _position_roster_fields("UNKNOWN_DRIVER", {})
    assert has_unit is False
    assert label is None
