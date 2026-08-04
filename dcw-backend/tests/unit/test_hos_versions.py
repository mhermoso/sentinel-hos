"""Tests for Geotab DutyStatusLog edit supersession handling."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.core.security import hash_canonical_log
from app.domains.dashboard.day_builder import RawHOSEvent, build_day_points, chicago_day_bounds
from app.domains.engine.replay import logs_to_timeline_events
from app.domains.ingestion.hos_versions import select_latest_hos_versions
from app.domains.ingestion.schemas import CanonicalDutyStatus, DCWCanonicalHOSLog


def _ts(hour: int) -> datetime:
    return datetime(2025, 7, 28, hour, 0, tzinfo=timezone.utc)


def test_hash_changes_when_geotab_marks_log_ignored() -> None:
    base = {
        "tenant_id": "t",
        "driver_id": "d1",
        "raw_id": "log-1",
        "status": "D",
        "event_timestamp": "2025-07-28T14:00:00+00:00",
        "device_id": "dev-1",
        "latitude": 41.8,
        "longitude": -87.6,
        "odometer_km": 1000.0,
        "raw_payload": {
            "version": 10,
            "isIgnored": False,
            "eventRecordStatus": 1,
        },
    }
    active_hash = hash_canonical_log(base)
    ignored = {
        **base,
        "raw_payload": {
            "version": 11,
            "isIgnored": True,
            "eventRecordStatus": 1,
        },
    }
    assert hash_canonical_log(ignored) != active_hash


def test_select_latest_hos_versions_keeps_newest_provider_version() -> None:
    older = SimpleNamespace(
        raw_id="log-1",
        event_timestamp=_ts(14),
        ingested_at=datetime(2025, 7, 28, 14, 1, tzinfo=timezone.utc),
        raw_payload={"version": 10, "isIgnored": False},
        status="D",
    )
    newer = SimpleNamespace(
        raw_id="log-1",
        event_timestamp=_ts(14),
        ingested_at=datetime(2025, 7, 28, 15, 0, tzinfo=timezone.utc),
        raw_payload={"version": 11, "isIgnored": True},
        status="D",
    )
    other = SimpleNamespace(
        raw_id="log-2",
        event_timestamp=_ts(16),
        ingested_at=datetime(2025, 7, 28, 16, 0, tzinfo=timezone.utc),
        raw_payload={"version": 1, "isIgnored": False},
        status="OFF",
    )
    latest = select_latest_hos_versions([older, other, newer])
    assert [r.raw_id for r in latest] == ["log-1", "log-2"]
    assert latest[0].raw_payload["isIgnored"] is True


def test_superseding_ignore_removes_status_from_timeline() -> None:
    """Active Driving later ignored must not remain on the duty timeline."""
    logs = [
        DCWCanonicalHOSLog(
            tenant_id="t",
            driver_id="d1",
            raw_id="log-1",
            status=CanonicalDutyStatus.DRIVING,
            event_timestamp=_ts(14),
            raw_payload={"version": 10, "isIgnored": False, "eventRecordStatus": 1},
        ),
        DCWCanonicalHOSLog(
            tenant_id="t",
            driver_id="d1",
            raw_id="log-1",
            status=CanonicalDutyStatus.DRIVING,
            event_timestamp=_ts(14),
            raw_payload={"version": 11, "isIgnored": True, "eventRecordStatus": 1},
        ),
        DCWCanonicalHOSLog(
            tenant_id="t",
            driver_id="d1",
            raw_id="log-2",
            status=CanonicalDutyStatus.OFF_DUTY,
            event_timestamp=_ts(16),
            raw_payload={"version": 1, "isIgnored": False, "eventRecordStatus": 1},
        ),
    ]
    events = logs_to_timeline_events(logs)
    assert [e.status for e in events] == ["OFF"]


def test_superseding_ignore_does_not_interrupt_day_grid() -> None:
    bounds = chicago_day_bounds(date(2025, 7, 28), ZoneInfo("America/Chicago"))
    events = [
        RawHOSEvent(
            "OFF",
            datetime(2025, 7, 27, 20, 0, tzinfo=timezone.utc),
            raw_id="carry",
            raw_payload={"version": 1},
        ),
        RawHOSEvent(
            "D",
            _ts(14),
            raw_id="edited",
            odometer_m=1000.0,
            raw_payload={"version": 10, "isIgnored": False, "eventRecordStatus": 1},
        ),
        RawHOSEvent(
            "D",
            _ts(14),
            raw_id="edited",
            odometer_m=1000.0,
            raw_payload={"version": 11, "isIgnored": True, "eventRecordStatus": 1},
        ),
        RawHOSEvent(
            "OFF",
            _ts(18),
            raw_id="off",
            odometer_m=1000.0,
            raw_payload={"version": 1, "eventRecordStatus": 1},
        ),
    ]
    grid, totals, _carry = build_day_points(events, bounds)
    statuses = [e["status"] for e in grid]
    assert "D" not in statuses
    # Carry OFF continues through the day until real OFF at 18:00 UTC.
    assert totals["OFF"] > 0
    assert totals["D"] == 0.0
