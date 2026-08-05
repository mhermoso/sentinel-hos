"""Unit tests for Home map unit filter predicates and GPS split."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.dashboard.home_unit_filters import (
    alert_stats_by_driver,
    has_driver,
    has_known_status,
    legend_status_key,
    matches_home_unit_filters,
    split_units_for_home,
    unit_has_usable_gps,
)
from app.domains.dashboard.schemas import (
    FleetAlertItemResponse,
    UnitListItemResponse,
)


def _unit(
    device_id: str,
    *,
    name: str | None = None,
    current_driver_id: str | None = None,
    current_driver_name: str | None = None,
    current_status: str | None = None,
    last_gps_at: datetime | None = None,
    last_gps_lat: float | None = None,
    last_gps_lon: float | None = None,
) -> UnitListItemResponse:
    return UnitListItemResponse(
        device_id=device_id,
        name=name,
        current_driver_id=current_driver_id,
        current_driver_name=current_driver_name,
        current_status=current_status,
        last_gps_at=last_gps_at,
        last_gps_lat=last_gps_lat,
        last_gps_lon=last_gps_lon,
    )


def test_legend_status_key_aliases() -> None:
    assert legend_status_key("PC") == "OFF"
    assert legend_status_key("YM") == "ON"
    assert legend_status_key("D") == "D"
    assert legend_status_key(None) == "UNKNOWN"
    assert legend_status_key("") == "UNKNOWN"
    assert legend_status_key("weird") == "UNKNOWN"


def test_has_driver_predicate() -> None:
    assert has_driver(_unit("v1", current_driver_id="d1")) is True
    assert has_driver(_unit("v2", current_driver_id=None)) is False
    assert has_driver(_unit("v3", current_driver_id="")) is False


def test_has_known_status_predicate() -> None:
    assert has_known_status(_unit("v1", current_status="D")) is True
    assert has_known_status(_unit("v2", current_status="PC")) is True
    assert has_known_status(_unit("v3", current_status="UNKNOWN")) is False
    assert has_known_status(_unit("v4", current_status=None)) is False
    assert has_known_status(_unit("v5", current_status="")) is False
    assert has_known_status(_unit("v6", current_status="FOO")) is False


def test_matches_home_unit_filters_defaults_off() -> None:
    bare = _unit("v1")
    assert matches_home_unit_filters(bare) is True
    assert matches_home_unit_filters(bare, has_driver_only=True, known_status_only=True) is False


def test_matches_home_unit_filters_default_home_view() -> None:
    active = _unit("v1", current_driver_id="d1", current_status="ON")
    no_driver = _unit("v2", current_status="D")
    unknown = _unit("v3", current_driver_id="d2", current_status="UNKNOWN")
    assert matches_home_unit_filters(active, has_driver_only=True, known_status_only=True) is True
    assert matches_home_unit_filters(no_driver, has_driver_only=True, known_status_only=True) is False
    assert matches_home_unit_filters(unknown, has_driver_only=True, known_status_only=True) is False
    assert matches_home_unit_filters(no_driver, has_driver_only=False, known_status_only=True) is True
    assert matches_home_unit_filters(unknown, has_driver_only=True, known_status_only=False) is True


def test_unit_has_usable_gps() -> None:
    ts = datetime(2026, 8, 1, tzinfo=UTC)
    assert unit_has_usable_gps(41.0, -87.0, ts) is True
    assert unit_has_usable_gps(0.0, 0.0, ts) is False
    assert unit_has_usable_gps(41.0, -87.0, None) is False
    assert unit_has_usable_gps(None, -87.0, ts) is False


def test_split_units_for_home_gps_and_device_ids() -> None:
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    units = [
        _unit(
            "dev-a",
            name="Unit A",
            current_driver_id="d1",
            current_status="D",
            last_gps_at=ts,
            last_gps_lat=41.8,
            last_gps_lon=-87.6,
        ),
        _unit("dev-b", name="No GPS"),
        _unit(
            "dev-c",
            name="Null island",
            last_gps_at=ts,
            last_gps_lat=0.0,
            last_gps_lon=0.0,
        ),
    ]
    with_gps, no_loc = split_units_for_home(units)
    assert [u.device_id for u in with_gps] == ["dev-a"]
    assert with_gps[0].latitude == 41.8
    assert with_gps[0].longitude == -87.6
    assert with_gps[0].current_driver_id == "d1"
    assert {u.device_id for u in no_loc} == {"dev-b", "dev-c"}


def test_split_units_enriches_alert_counts_from_current_driver() -> None:
    ts = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    units = [
        _unit(
            "dev-a",
            current_driver_id="d1",
            current_status="SB",
            last_gps_at=ts,
            last_gps_lat=30.0,
            last_gps_lon=-97.0,
        ),
        _unit(
            "dev-b",
            current_status="D",
            last_gps_at=ts,
            last_gps_lat=31.0,
            last_gps_lon=-96.0,
        ),
    ]
    stats = {"d1": (2, 1, "VIOLATION", "11h")}
    with_gps, _ = split_units_for_home(units, stats)
    by_id = {u.device_id: u for u in with_gps}
    assert by_id["dev-a"].warning_count == 2
    assert by_id["dev-a"].violation_count == 1
    assert by_id["dev-a"].latest_alert_type == "11h"
    assert by_id["dev-b"].warning_count == 0
    assert by_id["dev-b"].violation_count == 0


def test_alert_stats_by_driver() -> None:
    alerts = [
        FleetAlertItemResponse(
            as_of=datetime(2026, 8, 1, 10, tzinfo=UTC),
            local_timestamp="2026-08-01 05:00",
            driver_id="d1",
            violation_type="11h",
            severity="WARNING",
            source="live_audit",
        ),
        FleetAlertItemResponse(
            as_of=datetime(2026, 8, 1, 12, tzinfo=UTC),
            local_timestamp="2026-08-01 07:00",
            driver_id="d1",
            violation_type="14h",
            severity="VIOLATION",
            source="live_audit",
        ),
        FleetAlertItemResponse(
            as_of=datetime(2026, 8, 1, 11, tzinfo=UTC),
            local_timestamp="2026-08-01 06:00",
            driver_id="d2",
            violation_type="30m",
            severity="WARNING",
            source="live_audit",
        ),
    ]
    stats = alert_stats_by_driver(alerts)
    assert stats["d1"][0] == 1
    assert stats["d1"][1] == 1
    assert stats["d1"][2] == "VIOLATION"
    assert stats["d1"][3] == "14h"
    assert stats["d2"][0] == 1
    assert stats["d2"][1] == 0
