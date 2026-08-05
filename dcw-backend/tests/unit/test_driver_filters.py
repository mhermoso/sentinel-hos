"""Unit tests for driver list filters."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.dashboard.driver_filters import filter_drivers
from app.domains.dashboard.schemas import DriverListItemResponse


def _driver(
    driver_id: str,
    *,
    driver_name: str | None = None,
    is_live: bool = False,
    current_status: str | None = None,
) -> DriverListItemResponse:
    return DriverListItemResponse(
        driver_id=driver_id,
        driver_name=driver_name,
        tenant_id="t1",
        is_live=is_live,
        event_count=1,
        last_event_at=datetime(2026, 7, 1, tzinfo=UTC),
        current_status=current_status,
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
