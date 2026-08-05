"""Unit tests for Samsara odometer series helpers + HOS stamping."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.ingestion.adapters.samsara import (
    map_samsara_log_to_canonical,
    nearest_odometer_at_or_before,
    preferred_odometer_series,
    stamp_odometer_on_logs,
)

FLEET_ID = "samsara:9005155"


def test_preferred_odometer_series_prefers_obd() -> None:
    series = preferred_odometer_series(
        [{"time": "2026-08-04T12:00:00Z", "value": 1000}],
        [{"time": "2026-08-04T12:00:00Z", "value": 999}],
    )
    assert series == [(datetime(2026, 8, 4, 12, 0, tzinfo=UTC), 1000.0)]


def test_preferred_odometer_series_falls_back_to_gps() -> None:
    series = preferred_odometer_series(
        [],
        [{"time": "2026-08-04T12:00:00Z", "value": 5000}],
    )
    assert series == [(datetime(2026, 8, 4, 12, 0, tzinfo=UTC), 5000.0)]


def test_nearest_odometer_at_or_before() -> None:
    series = [
        (datetime(2026, 8, 4, 10, 0, tzinfo=UTC), 100.0),
        (datetime(2026, 8, 4, 12, 0, tzinfo=UTC), 200.0),
        (datetime(2026, 8, 4, 14, 0, tzinfo=UTC), 300.0),
    ]
    assert nearest_odometer_at_or_before(series, datetime(2026, 8, 4, 11, 0, tzinfo=UTC)) == 100.0
    assert nearest_odometer_at_or_before(series, datetime(2026, 8, 4, 12, 0, tzinfo=UTC)) == 200.0
    assert nearest_odometer_at_or_before(series, datetime(2026, 8, 4, 9, 0, tzinfo=UTC)) is None


def test_stamp_odometer_on_logs() -> None:
    log = map_samsara_log_to_canonical(
        FLEET_ID,
        {"id": "d1", "name": "Driver"},
        {
            "hosStatusType": "driving",
            "logStartTime": "2026-08-04T12:30:00.000Z",
            "vehicle": {"id": "v1"},
            "logRecordedLocation": {"latitude": 31.76, "longitude": -106.48},
        },
    )
    assert log.odometer_km is None
    stamped = stamp_odometer_on_logs(
        [log],
        {
            "v1": [
                (datetime(2026, 8, 4, 12, 0, tzinfo=UTC), 10_000.0),
                (datetime(2026, 8, 4, 13, 0, tzinfo=UTC), 20_000.0),
            ]
        },
    )
    assert stamped[0].odometer_km == 10_000.0
    assert stamped[0].raw_id == log.raw_id
