"""Unit tests for driver list filters (including assignment/profile)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.dashboard.driver_filters import (
    filter_drivers,
    matches_assignment,
    matches_on_unit,
    matches_profile,
)
from app.domains.dashboard.schemas import DriverListItemResponse
from app.domains.ingestion.roster import is_real_person_driver_id, is_unassigned_driver_id


def _driver(
    driver_id: str,
    *,
    driver_name: str | None = None,
    is_live: bool = False,
    current_status: str | None = None,
    roster_active: bool | None = None,
    profile_complete: bool | None = None,
    has_unit_assignment: bool | None = None,
    unit_label: str | None = None,
) -> DriverListItemResponse:
    return DriverListItemResponse(
        driver_id=driver_id,
        driver_name=driver_name,
        tenant_id="t1",
        is_live=is_live,
        event_count=1,
        last_event_at=datetime(2026, 7, 1, tzinfo=UTC),
        current_status=current_status,
        roster_active=roster_active,
        profile_complete=profile_complete,
        has_unit_assignment=has_unit_assignment,
        unit_label=unit_label,
    )


def test_filter_drivers_search_name_and_id() -> None:
    drivers = [
        _driver("b382", driver_name="Garza, Maria"),
        _driver("a100", driver_name="Smith, John"),
    ]
    assert len(filter_drivers(drivers, q="garza")) == 1
    assert filter_drivers(drivers, q="garza")[0].driver_id == "b382"
    assert len(filter_drivers(drivers, q="B382")) == 1
    assert len(filter_drivers(drivers, q="a100")) == 1
    assert len(filter_drivers(drivers, q="")) == 2
    assert len(filter_drivers(drivers, q="all")) == 2


def test_filter_drivers_status_and_mode() -> None:
    drivers = [
        _driver("d1", is_live=True, current_status="D"),
        _driver("d2", is_live=False, current_status="OFF"),
        _driver("d3", is_live=True, current_status=None),
    ]
    assert [d.driver_id for d in filter_drivers(drivers, status="D")] == ["d1"]
    assert [d.driver_id for d in filter_drivers(drivers, status="UNKNOWN")] == ["d3"]
    assert {d.driver_id for d in filter_drivers(drivers, mode="live")} == {"d1", "d3"}
    assert [d.driver_id for d in filter_drivers(drivers, mode="historical")] == ["d2"]


def test_filter_drivers_combined() -> None:
    drivers = [
        _driver("d1", driver_name="Alpha", is_live=True, current_status="D"),
        _driver("d2", driver_name="Beta", is_live=True, current_status="OFF"),
    ]
    result = filter_drivers(drivers, q="alpha", status="D", mode="live")
    assert len(result) == 1
    assert result[0].driver_id == "d1"


def test_hos_id_sentinels() -> None:
    assert is_unassigned_driver_id("unassigned:device:abc")
    assert is_unassigned_driver_id("UNKNOWN_DRIVER")
    assert not is_unassigned_driver_id("b382")
    assert is_real_person_driver_id("b382")
    assert not is_real_person_driver_id("unassigned:device:x")


def test_matches_assignment_assigned_requires_active_roster() -> None:
    person = _driver("b382", roster_active=True, profile_complete=True)
    no_roster = _driver("c100", roster_active=None)
    inactive = _driver("d200", roster_active=False)
    unassigned = _driver("unassigned:device:dev1")

    assert matches_assignment(person, "assigned")
    assert not matches_assignment(no_roster, "assigned")
    assert not matches_assignment(inactive, "assigned")
    assert not matches_assignment(unassigned, "assigned")
    assert matches_assignment(unassigned, "unassigned")
    assert matches_assignment(person, "all")
    assert matches_assignment(unassigned, None)


def test_matches_profile_complete() -> None:
    complete = _driver("a", roster_active=True, profile_complete=True)
    incomplete = _driver("b", roster_active=True, profile_complete=False)
    missing = _driver("c", roster_active=True, profile_complete=None)
    unassigned = _driver("unassigned:device:x")

    assert matches_profile(complete, "complete")
    assert not matches_profile(incomplete, "complete")
    assert matches_profile(incomplete, "incomplete")
    assert matches_profile(missing, "incomplete")
    assert not matches_profile(unassigned, "incomplete")
    assert matches_profile(complete, "all")


def test_matches_on_unit() -> None:
    on = _driver("a", has_unit_assignment=True)
    off = _driver("b", has_unit_assignment=False)
    assert matches_on_unit(on, True)
    assert not matches_on_unit(off, True)
    assert matches_on_unit(off, None)
    assert matches_on_unit(off, False)


def test_filter_drivers_default_assigned_complete() -> None:
    drivers = [
        _driver(
            "ok",
            driver_name="Ok Driver",
            roster_active=True,
            profile_complete=True,
            has_unit_assignment=True,
        ),
        _driver(
            "no_phone",
            driver_name="No Phone",
            roster_active=True,
            profile_complete=False,
        ),
        _driver("unassigned:device:truck1"),
        _driver("UNKNOWN_DRIVER"),
        _driver("orphan", roster_active=None),
    ]
    result = filter_drivers(drivers, assignment="assigned", profile="complete")
    assert [d.driver_id for d in result] == ["ok"]

    unassigned = filter_drivers(drivers, assignment="unassigned")
    assert {d.driver_id for d in unassigned} == {
        "unassigned:device:truck1",
        "UNKNOWN_DRIVER",
    }

    on_unit = filter_drivers(
        drivers, assignment="assigned", profile="complete", on_unit=True
    )
    assert [d.driver_id for d in on_unit] == ["ok"]


def test_filter_drivers_search_unit_label() -> None:
    drivers = [
        _driver("a", driver_name="Alpha", unit_label="Unit 42", roster_active=True),
        _driver("b", driver_name="Beta", unit_label="Unit 7", roster_active=True),
    ]
    assert [d.driver_id for d in filter_drivers(drivers, q="unit 42")] == ["a"]
