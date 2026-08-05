"""Unit tests for dashboard DriverContactProfile wiring."""

from __future__ import annotations

from app.domains.dashboard.profile import build_contact_profile
from app.domains.ingestion.schemas import DriverRosterEntry


def _roster(**kwargs: object) -> DriverRosterEntry:
    base = {
        "provider": "geotab",
        "tenant_id": "fleet1",
        "external_driver_id": "b382",
        "display_name": "Maria Garza",
        "first_name": "Maria",
        "last_name": "Garza",
        "phone_e164": "+19155551212",
        "current_device_id": "dev-9",
        "unit_label": "Truck 9",
        "is_active": True,
        "profile_complete": True,
        "has_unit_assignment": True,
    }
    base.update(kwargs)
    return DriverRosterEntry(**base)  # type: ignore[arg-type]


def test_build_contact_profile_from_roster() -> None:
    profile = build_contact_profile("b382", _roster())
    assert profile.roster_found is True
    assert profile.display_name == "Maria Garza"
    assert profile.phone_e164 == "+19155551212"
    assert profile.current_device_id == "dev-9"
    assert profile.unit_label == "Truck 9"
    assert profile.profile_complete is True
    assert profile.has_unit_assignment is True
    assert profile.is_active is True


def test_build_contact_profile_missing_roster() -> None:
    profile = build_contact_profile("unknown", None)
    assert profile.roster_found is False
    assert profile.driver_id == "unknown"
    assert profile.phone_e164 is None
    assert profile.profile_complete is None
    assert profile.has_unit_assignment is None


def test_build_contact_profile_incomplete() -> None:
    profile = build_contact_profile(
        "b1",
        _roster(
            external_driver_id="b1",
            phone_e164=None,
            profile_complete=False,
            has_unit_assignment=False,
            current_device_id=None,
            unit_label=None,
        ),
    )
    assert profile.roster_found is True
    assert profile.profile_complete is False
    assert profile.has_unit_assignment is False
    assert profile.phone_e164 is None
