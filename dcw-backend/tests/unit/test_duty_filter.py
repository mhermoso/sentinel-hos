"""Unit tests for ignore / inactive Geotab duty-status filtering."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.domains.dashboard.day_builder import RawHOSEvent, build_day_points, chicago_day_bounds
from app.domains.engine.replay import logs_to_timeline_events
from app.domains.ingestion.duty_filter import should_skip_duty_status_change
from app.domains.ingestion.schemas import CanonicalDutyStatus, DCWCanonicalHOSLog


def test_skip_ignored_and_inactive_statuses() -> None:
    assert should_skip_duty_status_change("D", {"isIgnored": True}) is True
    assert should_skip_duty_status_change("D", {"eventRecordStatus": 2}) is True
    assert should_skip_duty_status_change("D", {"eventRecordStatus": 3}) is True
    assert should_skip_duty_status_change("D", {"eventRecordStatus": 4}) is True
    assert should_skip_duty_status_change("D", {"eventRecordStatus": 1}) is False
    assert should_skip_duty_status_change("D", {"isIgnored": False, "eventRecordStatus": 1}) is False
    assert should_skip_duty_status_change("UNKNOWN", {"eventRecordStatus": 1}) is True


def test_ignored_log_does_not_interrupt_day_timeline() -> None:
    """isIgnored D→noise→OFF keeps D until real OFF (like UNKNOWN)."""
    bounds = chicago_day_bounds(date(2025, 7, 28), ZoneInfo("America/Chicago"))
    events = [
        RawHOSEvent("OFF", datetime(2025, 7, 27, 20, 0, tzinfo=UTC)),
        RawHOSEvent("D", datetime(2025, 7, 28, 14, 0, tzinfo=UTC), odometer_m=1000.0),
        RawHOSEvent(
            "ON",
            datetime(2025, 7, 28, 16, 0, tzinfo=UTC),
            odometer_m=5000.0,
            raw_payload={"isIgnored": True, "eventRecordStatus": 1},
        ),
        RawHOSEvent(
            "SB",
            datetime(2025, 7, 28, 17, 0, tzinfo=UTC),
            odometer_m=6000.0,
            raw_payload={"eventRecordStatus": 2},
        ),
        RawHOSEvent("OFF", datetime(2025, 7, 28, 18, 0, tzinfo=UTC), odometer_m=6000.0),
    ]
    grid, totals, _carry = build_day_points(events, bounds)
    statuses = [e["status"] for e in grid]
    assert "ON" not in statuses
    assert "SB" not in statuses
    # D from 14:00 UTC to 18:00 UTC = 4h
    assert totals["D"] == 4 * 3600.0
    # Odometer delta skips ignored: 6000-1000 over D stretch
    d_seg = next(e for e in grid if e["status"] == "D")
    assert d_seg["distance_m"] == 5000.0


def test_logs_to_timeline_skips_ignored() -> None:
    logs = [
        DCWCanonicalHOSLog(
            tenant_id="t",
            driver_id="d1",
            raw_id="1",
            status=CanonicalDutyStatus.DRIVING,
            event_timestamp=datetime(2025, 7, 28, 14, 0, tzinfo=UTC),
            raw_payload={"eventRecordStatus": 1},
        ),
        DCWCanonicalHOSLog(
            tenant_id="t",
            driver_id="d1",
            raw_id="2",
            status=CanonicalDutyStatus.ON_DUTY,
            event_timestamp=datetime(2025, 7, 28, 15, 0, tzinfo=UTC),
            raw_payload={"isIgnored": True},
        ),
        DCWCanonicalHOSLog(
            tenant_id="t",
            driver_id="d1",
            raw_id="3",
            status=CanonicalDutyStatus.OFF_DUTY,
            event_timestamp=datetime(2025, 7, 28, 16, 0, tzinfo=UTC),
            raw_payload={"eventRecordStatus": 1},
        ),
    ]
    events = logs_to_timeline_events(logs)
    assert [e.status for e in events] == ["D", "OFF"]
