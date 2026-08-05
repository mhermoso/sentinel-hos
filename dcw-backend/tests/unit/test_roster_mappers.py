"""Unit tests for Geotab/Samsara roster mappers + Motive protocol stubs."""

from __future__ import annotations

import pytest

from app.domains.ingestion.adapters.geotab import (
    geotab_assignment_from_duty_logs,
    map_geotab_device_to_roster_entry,
    map_geotab_user_to_roster_entry,
)
from app.domains.ingestion.adapters.motive import MotiveAdapter
from app.domains.ingestion.adapters.samsara import (
    map_samsara_driver_to_roster_entry,
    map_samsara_vehicle_to_roster_entry,
    samsara_vehicle_assignment_from_driver,
)
from app.domains.ingestion.roster import (
    derive_profile_complete,
    normalize_phone_e164,
)


def test_normalize_phone_e164() -> None:
    assert normalize_phone_e164("555-123-4567") == "+15551234567"
    assert normalize_phone_e164("+1 (555) 123-4567") == "+15551234567"
    assert normalize_phone_e164("15551234567") == "+15551234567"
    assert normalize_phone_e164("") is None
    assert normalize_phone_e164(None) is None


def test_derive_profile_complete_modes() -> None:
    assert derive_profile_complete(
        first_name="Ada",
        last_name="Lovelace",
        display_name="Ada Lovelace",
        phone_e164="+15551234567",
        require_first_last=True,
    )
    assert not derive_profile_complete(
        first_name="Ada",
        last_name=None,
        display_name="Ada",
        phone_e164="+15551234567",
        require_first_last=True,
    )
    assert derive_profile_complete(
        first_name=None,
        last_name=None,
        display_name="MARCOS RAMOS",
        phone_e164="+15551234567",
        require_first_last=False,
    )


def test_map_geotab_user_complete_with_unit() -> None:
    entry = map_geotab_user_to_roster_entry(
        {
            "id": "b382",
            "firstName": "Maria",
            "lastName": "Garza",
            "name": "mgarza",
            "phoneNumber": "9155551212",
            "isDriver": True,
        },
        tenant_id="fleet_a",
        current_device_id="dev-9",
        unit_label="Truck 9",
    )
    assert entry is not None
    assert entry.provider == "geotab"
    assert entry.external_driver_id == "b382"
    assert entry.first_name == "Maria"
    assert entry.last_name == "Garza"
    assert entry.phone_e164 == "+19155551212"
    assert entry.profile_complete is True
    assert entry.has_unit_assignment is True
    assert entry.unit_label == "Truck 9"
    assert entry.is_active is True


def test_map_geotab_user_skips_non_driver() -> None:
    assert (
        map_geotab_user_to_roster_entry(
            {"id": "x", "isDriver": False, "firstName": "A", "lastName": "B"},
            tenant_id="t",
        )
        is None
    )


def test_map_geotab_user_incomplete_without_phone() -> None:
    entry = map_geotab_user_to_roster_entry(
        {
            "id": "b1",
            "firstName": "Ada",
            "lastName": "Lovelace",
            "isDriver": True,
        },
        tenant_id="t",
    )
    assert entry is not None
    assert entry.profile_complete is False


def test_map_geotab_device() -> None:
    entry = map_geotab_device_to_roster_entry(
        {
            "id": "dev1",
            "name": "Unit 12",
            "vehicleIdentificationNumber": "VIN123",
        },
        tenant_id="t",
        current_driver_id="b382",
    )
    assert entry is not None
    assert entry.external_device_id == "dev1"
    assert entry.name == "Unit 12"
    assert entry.vin == "VIN123"
    assert entry.current_driver_id == "b382"


def test_geotab_assignment_from_duty_logs() -> None:
    logs = [
        {"driver": {"id": "d1"}, "device": {"id": "v1"}, "dateTime": "2026-01-01T00:00:00Z"},
        {"driver": "NoUserId", "device": {"id": "v9"}, "dateTime": "2026-01-01T01:00:00Z"},
        {"driver": {"id": "d1"}, "device": {"id": "v2"}, "dateTime": "2026-01-01T02:00:00Z"},
        {"driver": {"id": "d2"}, "device": {"id": "v1"}, "dateTime": "2026-01-01T03:00:00Z"},
    ]
    driver_to_device, device_to_driver = geotab_assignment_from_duty_logs(logs)
    assert driver_to_device["d1"] == "v2"
    assert driver_to_device["d2"] == "v1"
    assert device_to_driver["v2"] == "d1"
    assert device_to_driver["v1"] == "d2"


def test_samsara_vehicle_assignment_prefers_current() -> None:
    assert (
        samsara_vehicle_assignment_from_driver(
            {
                "currentVehicle": {"id": "111"},
                "staticAssignedVehicle": {"id": "222"},
            }
        )
        == "111"
    )
    assert (
        samsara_vehicle_assignment_from_driver({"staticAssignedVehicle": {"id": "222"}})
        == "222"
    )
    assert samsara_vehicle_assignment_from_driver({"staticAssignedVehicle": {"id": "0"}}) is None


def test_map_samsara_driver_complete() -> None:
    entry = map_samsara_driver_to_roster_entry(
        {
            "id": "52501234",
            "name": "MARCOS RAMOS",
            "phone": "915-555-9999",
            "driverActivationStatus": "active",
            "staticAssignedVehicle": {"id": "281474990467032"},
        },
        tenant_id="samsara:9005155",
        unit_label="Unit A",
    )
    assert entry is not None
    assert entry.provider == "samsara"
    assert entry.display_name == "MARCOS RAMOS"
    assert entry.first_name == "MARCOS"
    assert entry.last_name == "RAMOS"
    assert entry.phone_e164 == "+19155559999"
    assert entry.profile_complete is True
    assert entry.has_unit_assignment is True
    assert entry.current_device_id == "281474990467032"
    assert entry.is_active is True


def test_map_samsara_driver_hos_vehicle_fallback() -> None:
    entry = map_samsara_driver_to_roster_entry(
        {
            "id": "1",
            "name": "Solo Name",
            "phone": "5551234567",
            "driverActivationStatus": "active",
        },
        tenant_id="samsara:1",
        hos_vehicle_id="999",
    )
    assert entry is not None
    assert entry.current_device_id == "999"
    assert entry.has_unit_assignment is True


def test_map_samsara_driver_deactivated() -> None:
    entry = map_samsara_driver_to_roster_entry(
        {
            "id": "2",
            "name": "Old Driver",
            "phone": "5551234567",
            "driverActivationStatus": "deactivated",
        },
        tenant_id="samsara:1",
    )
    assert entry is not None
    assert entry.is_active is False
    assert entry.profile_complete is True


def test_map_samsara_vehicle() -> None:
    entry = map_samsara_vehicle_to_roster_entry(
        {"id": "v1", "name": "Truck", "vin": "VIN"},
        tenant_id="samsara:1",
        current_driver_id="52501234",
    )
    assert entry is not None
    assert entry.external_device_id == "v1"
    assert entry.current_driver_id == "52501234"
    assert map_samsara_vehicle_to_roster_entry({"id": "0"}, tenant_id="t") is None


@pytest.mark.asyncio
async def test_motive_roster_stubs_raise() -> None:
    adapter = MotiveAdapter()
    with pytest.raises(NotImplementedError):
        await adapter.connect()
    with pytest.raises(NotImplementedError):
        await adapter.fetch_feed("motive:1", "")
    with pytest.raises(NotImplementedError):
        await adapter.fetch_driver_roster("motive:1")
    with pytest.raises(NotImplementedError):
        await adapter.fetch_vehicle_roster("motive:1")
