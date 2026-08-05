"""Unit tests for Samsara HOS + GPS mappers (canonical / breadcrumb)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.domains.ingestion.adapters.samsara import (
    map_samsara_gps_to_breadcrumb,
    map_samsara_log_to_canonical,
)
from app.domains.ingestion.schemas import CanonicalDutyStatus

FLEET_ID = "samsara:9005155"


def _driver(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "52501234", "name": "MARCOS RAMOS"}
    base.update(overrides)
    return base


def _hos_entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "hosStatusType": "driving",
        "logStartTime": "2026-08-04T14:30:00.000Z",
        "logEndTime": "2026-08-04T15:00:00.000Z",
        "vehicle": {"id": "281474990467032"},
        "logRecordedLocation": {"latitude": 31.7619, "longitude": -106.4850},
        "remark": "Pre-trip inspection",
        "token": "should-be-masked",
    }
    base.update(overrides)
    return base


def test_map_samsara_log_full_payload() -> None:
    log = map_samsara_log_to_canonical(FLEET_ID, _driver(), _hos_entry())
    assert log.tenant_id == FLEET_ID
    assert log.driver_id == "52501234"
    assert log.driver_name == "MARCOS RAMOS"
    assert log.status is CanonicalDutyStatus.DRIVING
    assert log.event_timestamp == datetime(2026, 8, 4, 14, 30, 0, tzinfo=UTC)
    assert log.device_id == "281474990467032"
    assert log.latitude == 31.7619
    assert log.longitude == -106.4850
    assert log.annotation == "Pre-trip inspection"
    assert log.raw_id == "samsara:52501234:2026-08-04T14:30:00.000Z:driving"
    assert log.raw_payload["token"] == "[MASKED]"
    assert log.raw_payload["driver"]["name"] == "MARCOS RAMOS"


def test_map_samsara_log_zero_zero_location_sentinel() -> None:
    log = map_samsara_log_to_canonical(
        FLEET_ID,
        _driver(),
        _hos_entry(logRecordedLocation={"latitude": 0, "longitude": 0}),
    )
    assert log.latitude is None
    assert log.longitude is None


def test_map_samsara_log_vehicle_id_zero_sentinel() -> None:
    log = map_samsara_log_to_canonical(
        FLEET_ID,
        _driver(),
        _hos_entry(vehicle={"id": "0"}),
    )
    assert log.device_id is None


def test_map_samsara_log_missing_vehicle() -> None:
    entry = _hos_entry()
    del entry["vehicle"]
    log = map_samsara_log_to_canonical(FLEET_ID, _driver(), entry)
    assert log.device_id is None


def test_map_samsara_log_remark_trimmed() -> None:
    log = map_samsara_log_to_canonical(
        FLEET_ID,
        _driver(),
        _hos_entry(remark="  late arrival  "),
    )
    assert log.annotation == "late arrival"


def test_map_samsara_log_synthetic_raw_id_includes_status() -> None:
    """Edited logs at the same timestamp keep distinct raw_ids via status."""
    driver = _driver()
    ts = "2026-08-04T10:00:00.000Z"
    off = map_samsara_log_to_canonical(
        FLEET_ID, driver, _hos_entry(hosStatusType="offDuty", logStartTime=ts)
    )
    on = map_samsara_log_to_canonical(
        FLEET_ID, driver, _hos_entry(hosStatusType="onDuty", logStartTime=ts)
    )
    assert off.raw_id == f"samsara:52501234:{ts}:offDuty"
    assert on.raw_id == f"samsara:52501234:{ts}:onDuty"
    assert off.raw_id != on.raw_id


def test_map_samsara_gps_mph_to_kmh_and_raw_id() -> None:
    crumb = map_samsara_gps_to_breadcrumb(
        fleet_id=FLEET_ID,
        vehicle_id="281474990467032",
        gps_entry={
            "time": "2026-08-04T16:00:00.123Z",
            "latitude": 31.76191234,
            "longitude": -106.48509876,
            "speedMilesPerHour": 55.0,
        },
        driver_id="52501234",
    )
    assert crumb.device_id == "281474990467032"
    assert crumb.driver_id == "52501234"
    assert crumb.raw_id == "samsara:gps:281474990467032:2026-08-04T16:00:00.123Z"
    assert crumb.speed_kmh == pytest.approx(55.0 * 1.609344)
    assert crumb.latitude == 31.7619
    assert crumb.longitude == -106.4851
    assert crumb.odometer_m is None
    assert crumb.event_timestamp == datetime(2026, 8, 4, 16, 0, 0, tzinfo=UTC)


def test_map_samsara_gps_with_odometer_meters() -> None:
    crumb = map_samsara_gps_to_breadcrumb(
        fleet_id=FLEET_ID,
        vehicle_id="281474990467032",
        gps_entry={
            "time": "2026-08-04T16:00:00Z",
            "latitude": 31.76,
            "longitude": -106.48,
            "odometerMeters": 1_250_000,
        },
        driver_id="52501234",
    )
    assert crumb.odometer_m == 1_250_000.0


def test_map_samsara_gps_rejects_zero_zero() -> None:
    with pytest.raises(ValueError, match="0,0"):
        map_samsara_gps_to_breadcrumb(
            fleet_id=FLEET_ID,
            vehicle_id="281474990467032",
            gps_entry={
                "time": "2026-08-04T16:00:00Z",
                "latitude": 0,
                "longitude": 0,
                "speedMilesPerHour": 0,
            },
            driver_id="unassigned:device:281474990467032",
        )


def test_map_samsara_gps_missing_speed_ok() -> None:
    crumb = map_samsara_gps_to_breadcrumb(
        fleet_id=FLEET_ID,
        vehicle_id="v1",
        gps_entry={
            "time": "2026-08-04T16:00:00Z",
            "latitude": 31.76,
            "longitude": -106.48,
        },
        driver_id="d1",
    )
    assert crumb.speed_kmh is None
