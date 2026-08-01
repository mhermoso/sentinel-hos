"""Unit tests for Geotab LogRecord → GPS breadcrumb mapping (ADR-007)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.security import hash_gps_breadcrumb
from app.domains.ingestion.adapters.geotab import map_geotab_log_record_to_breadcrumb
from app.domains.ingestion.models import GpsBreadcrumbRecord
from app.domains.ingestion.repository import IngestionRepository
from app.domains.ingestion.schemas import DCWGpsBreadcrumb


def _sample_log_record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "b1",
        "device": {"id": "dev-9"},
        "dateTime": "2026-07-30T12:34:56.789Z",
        "latitude": 41.87811234,
        "longitude": -87.62979876,
        "speed": 55.5,
        "x": -87.62979876,
        "y": 41.87811234,
    }
    base.update(overrides)
    return base


def test_map_log_record_rounds_gps_and_truncates_timestamp() -> None:
    crumb = map_geotab_log_record_to_breadcrumb(
        _sample_log_record(),
        tenant_id="bbbBros",
        driver_id="drv-1",
    )
    assert crumb.latitude == 41.8781
    assert crumb.longitude == -87.6298
    assert crumb.event_timestamp == datetime(2026, 7, 30, 12, 34, 56, tzinfo=UTC)
    assert crumb.device_id == "dev-9"
    assert crumb.driver_id == "drv-1"
    assert crumb.raw_id == "b1"
    assert crumb.speed_kmh == 55.5
    assert "password" not in crumb.raw_payload or crumb.raw_payload.get("password") == "[MASKED]"


def test_map_log_record_accepts_xy_fields() -> None:
    raw = {
        "id": "b2",
        "device": {"id": "dev-2"},
        "dateTime": datetime(2026, 7, 1, 8, 0, 0, 123456, tzinfo=UTC),
        "x": -90.123456,
        "y": 38.654321,
    }
    crumb = map_geotab_log_record_to_breadcrumb(raw, tenant_id="t1", driver_id="d1")
    assert crumb.latitude == 38.6543
    assert crumb.longitude == -90.1235
    assert crumb.event_timestamp.microsecond == 0
    assert crumb.speed_kmh is None


def test_map_log_record_rejects_missing_gps() -> None:
    with pytest.raises(ValueError, match="latitude/longitude"):
        map_geotab_log_record_to_breadcrumb(
            {
                "id": "b3",
                "device": {"id": "dev-3"},
                "dateTime": "2026-07-30T00:00:00Z",
            },
            tenant_id="t1",
            driver_id="d1",
        )


def test_map_log_record_rejects_missing_device() -> None:
    with pytest.raises(ValueError, match="device"):
        map_geotab_log_record_to_breadcrumb(
            {
                "id": "b4",
                "dateTime": "2026-07-30T00:00:00Z",
                "latitude": 1.0,
                "longitude": 2.0,
            },
            tenant_id="t1",
            driver_id="d1",
        )


@pytest.mark.asyncio
async def test_resolve_driver_for_device_fallback() -> None:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)

    repo = IngestionRepository(session)
    as_of = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    driver_id = await repo.resolve_driver_for_device(
        tenant_id="t1",
        device_id="dev-x",
        as_of=as_of,
    )
    assert driver_id == "unassigned:device:dev-x"


@pytest.mark.asyncio
async def test_resolve_driver_for_device_uses_cache() -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    repo = IngestionRepository(session)
    cache = {"dev-cached": "drv-cached"}
    as_of = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    driver_id = await repo.resolve_driver_for_device(
        tenant_id="t1",
        device_id="dev-cached",
        as_of=as_of,
        cache=cache,
    )
    assert driver_id == "drv-cached"
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_driver_for_device_from_hos() -> None:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = "drv-from-hos"
    session.execute = AsyncMock(return_value=result)

    repo = IngestionRepository(session)
    cache: dict[str, str] = {}
    as_of = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    driver_id = await repo.resolve_driver_for_device(
        tenant_id="t1",
        device_id="dev-y",
        as_of=as_of,
        cache=cache,
    )
    assert driver_id == "drv-from-hos"
    assert cache["dev-y"] == "drv-from-hos"


@pytest.mark.asyncio
async def test_resolve_driver_for_device_does_not_cache_unassigned() -> None:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)

    repo = IngestionRepository(session)
    cache: dict[str, str] = {}
    as_of = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    driver_id = await repo.resolve_driver_for_device(
        tenant_id="t1",
        device_id="dev-x",
        as_of=as_of,
        cache=cache,
    )
    assert driver_id == "unassigned:device:dev-x"
    assert "dev-x" not in cache

    result.scalar_one_or_none.return_value = "drv-late"
    driver_id_late = await repo.resolve_driver_for_device(
        tenant_id="t1",
        device_id="dev-x",
        as_of=as_of,
        cache=cache,
    )
    assert driver_id_late == "drv-late"
    assert cache["dev-x"] == "drv-late"
    assert session.execute.await_count == 2


def _mock_crumb(
    raw_id: str,
    driver_id: str,
    device_id: str,
    ts: datetime,
) -> MagicMock:
    crumb = MagicMock(spec=GpsBreadcrumbRecord)
    crumb.raw_id = raw_id
    crumb.driver_id = driver_id
    crumb.device_id = device_id
    crumb.event_timestamp = ts
    crumb.latitude = 41.0
    crumb.longitude = -87.0
    return crumb


@pytest.mark.asyncio
async def test_get_gps_breadcrumbs_for_driver_day_route_includes_device_trails() -> None:
    session = MagicMock()
    device_result = MagicMock()
    device_result.scalars.return_value.all.return_value = ["b1"]

    gps_result = MagicMock()
    ts = datetime(2026, 7, 30, 14, 0, tzinfo=UTC)
    gps_result.scalars.return_value.all.return_value = [
        _mock_crumb("r1", "b382", "b1", ts),
        _mock_crumb("r2", "unassigned:device:b1", "b1", ts),
        _mock_crumb("r1", "b382", "b1", ts),
    ]

    session.execute = AsyncMock(side_effect=[device_result, gps_result])

    repo = IngestionRepository(session)
    start = datetime(2026, 7, 30, 5, 0, tzinfo=UTC)
    end = datetime(2026, 7, 31, 5, 0, tzinfo=UTC)
    result = await repo.get_gps_breadcrumbs_for_driver_day_route(
        tenant_id="bbbBros",
        driver_id="b382",
        start_utc=start,
        end_utc=end,
    )
    assert len(result) == 2
    assert [c.raw_id for c in result] == ["r1", "r2"]
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_get_gps_breadcrumbs_for_driver_day_route_no_hos_devices() -> None:
    session = MagicMock()
    device_result = MagicMock()
    device_result.scalars.return_value.all.return_value = []

    gps_result = MagicMock()
    ts = datetime(2026, 7, 30, 14, 0, tzinfo=UTC)
    gps_result.scalars.return_value.all.return_value = [
        _mock_crumb("r1", "b382", "b1", ts),
    ]

    session.execute = AsyncMock(side_effect=[device_result, gps_result])

    repo = IngestionRepository(session)
    start = datetime(2026, 7, 30, 5, 0, tzinfo=UTC)
    end = datetime(2026, 7, 31, 5, 0, tzinfo=UTC)
    result = await repo.get_gps_breadcrumbs_for_driver_day_route(
        tenant_id="bbbBros",
        driver_id="b382",
        start_utc=start,
        end_utc=end,
    )
    assert len(result) == 1
    assert result[0].driver_id == "b382"


@pytest.mark.asyncio
async def test_persist_gps_breadcrumbs_dedup_on_conflict() -> None:
    """Second insert for same (tenant_id, raw_id) should not raise; rowcount 0."""
    session = MagicMock()
    first = MagicMock()
    first.rowcount = 1
    second = MagicMock()
    second.rowcount = 0
    session.execute = AsyncMock(side_effect=[first, second])
    session.flush = AsyncMock()

    repo = IngestionRepository(session)
    crumb = map_geotab_log_record_to_breadcrumb(
        _sample_log_record(),
        tenant_id="bbbBros",
        driver_id="drv-1",
    )
    inserted = await repo.persist_gps_breadcrumbs([crumb, crumb])
    assert inserted == 1
    assert session.execute.await_count == 2


def test_hash_gps_breadcrumb_stable() -> None:
    crumb = DCWGpsBreadcrumb(
        tenant_id="t1",
        device_id="d1",
        driver_id="drv",
        raw_id="r1",
        event_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        latitude=1.2345,
        longitude=-2.3456,
        speed_kmh=10.0,
        raw_payload={"id": "r1"},
    )
    h1 = hash_gps_breadcrumb(crumb.model_dump(mode="json"))
    h2 = hash_gps_breadcrumb(crumb.model_dump(mode="json"))
    assert h1 == h2
    assert len(h1) == 64
