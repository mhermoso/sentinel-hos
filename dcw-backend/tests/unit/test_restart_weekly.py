"""Unit tests for 34-hour restart weekly cycle reset."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.security import compute_inputs_hash
from app.domains.engine.replay import (
    compute_weekly_duty_seconds,
    count_1_to_5_am_periods,
    find_restart_reset_point,
    is_valid_restart_period,
    logs_to_timeline_events,
)
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline, ViolationType
from app.domains.ingestion.schemas import CanonicalDutyStatus, DCWCanonicalHOSLog

UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")
_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "hos_30d_canonical.json"
if not _DATA_PATH.exists():
    _DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "hos_10d_canonical.json"


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _heavy_duty_then_restart_timeline() -> list[DriverTimeline.HOSEvent]:
    """70h duty, then 35h OFF fully including two Chicago 1–5 AM days, then ON."""
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(2026, 7, 10, 0)),
    ]
    # Start early enough that OFF begins before 01:00 CDT on the first restart morning.
    duty_start = _ts(2026, 7, 10, 6)
    events.append(DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=duty_start))
    # 70 hours of driving segments (simplified as one long block ending before restart)
    events.append(
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=duty_start + timedelta(hours=70),
        )
    )
    restart_start = duty_start + timedelta(hours=70)  # Jul 13 04:00 UTC = Jul 12 23:00 CDT
    # 35h OFF — fully includes Jul 13 and Jul 14 01:00–05:00 CDT
    events.append(
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.ON_DUTY.value,
            timestamp=restart_start + timedelta(hours=35),
        )
    )
    return events


def test_count_1_to_5_am_periods_two_days() -> None:
    start = _ts(2026, 7, 25, 6, 0)  # 01:00 CDT
    end = _ts(2026, 7, 27, 6, 0)
    assert count_1_to_5_am_periods(start, end, CHICAGO) >= 2


def test_partial_1_to_5_am_overlap_does_not_count() -> None:
    """One minute of the first 1–5 AM window must not count as a full period."""
    # 04:59–14:59 CDT is exactly 34h, but day-1 includes only 04:59–05:00.
    off_start = _ts(2026, 7, 20, 9, 59)  # 04:59 CDT
    on_duty = off_start + timedelta(hours=34)  # 14:59 CDT next day
    assert count_1_to_5_am_periods(off_start, on_duty, CHICAGO) == 1
    assert not is_valid_restart_period(off_start, on_duty, home_terminal_tz=CHICAGO)

    duty_start = on_duty - timedelta(hours=34) - timedelta(hours=70)
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=duty_start - timedelta(hours=1)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=duty_start),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=off_start),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.ON_DUTY.value, timestamp=on_duty),
    ]
    assert find_restart_reset_point(events, on_duty, home_terminal_tz=CHICAGO) is None
    weekly = compute_weekly_duty_seconds(
        events, as_of=on_duty, cycle_days=8, home_terminal_tz=CHICAGO
    )
    assert weekly == pytest.approx(70 * 3600.0)


def test_full_1_to_5_am_periods_qualify_restart() -> None:
    """Rest that fully covers two 1–5 AM windows must credit the restart."""
    # 00:00 CDT day1 → 10:00 CDT day2 = 34h, covers Jul 20 and Jul 21 1–5 AM fully.
    off_start = _ts(2026, 7, 20, 5, 0)  # 00:00 CDT
    on_duty = off_start + timedelta(hours=34)
    assert count_1_to_5_am_periods(off_start, on_duty, CHICAGO) == 2
    assert is_valid_restart_period(off_start, on_duty, home_terminal_tz=CHICAGO)


def test_34h_restart_resets_weekly_duty() -> None:
    events = _heavy_duty_then_restart_timeline()
    as_of = events[-1].timestamp + timedelta(hours=1)
    weekly = compute_weekly_duty_seconds(
        events,
        as_of=as_of,
        cycle_days=8,
        home_terminal_tz=CHICAGO,
    )
    assert weekly < 70 * 3600


def test_restart_without_two_am_periods_no_reset() -> None:
    """34h OFF spanning only one 1–5 AM home-terminal day does not reset weekly duty."""
    # 34h rest Jul 21 11:00 UTC → Jul 22 21:00 UTC (only Jul 22 1–5 AM CDT overlaps)
    off_start = _ts(2026, 7, 21, 11)
    on_duty = off_start + timedelta(hours=34)
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(2026, 7, 20, 0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(2026, 7, 20, 10)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=off_start),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.ON_DUTY.value, timestamp=on_duty),
    ]
    as_of = on_duty
    assert count_1_to_5_am_periods(off_start, on_duty, CHICAGO) == 1
    reset = find_restart_reset_point(events, as_of, home_terminal_tz=CHICAGO)
    assert reset is None
    weekly = compute_weekly_duty_seconds(events, as_of=as_of, cycle_days=8, home_terminal_tz=CHICAGO)
    assert weekly == pytest.approx(25 * 3600.0)


def test_rolling_window_after_restart_ages_out() -> None:
    """Duty more than 8 days after a valid restart uses only the rolling window."""
    restart_on = _ts(2026, 1, 1, 12)
    duty_start = _ts(2026, 1, 4, 8)
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(2025, 12, 30, 0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=restart_on),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=restart_on + timedelta(hours=4),
        ),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=duty_start),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=duty_start + timedelta(hours=4),
        ),
    ]
    as_of = restart_on + timedelta(days=10)
    weekly = compute_weekly_duty_seconds(events, as_of=as_of, cycle_days=8, home_terminal_tz=CHICAGO)
    assert weekly == pytest.approx(4 * 3600.0)


@pytest.mark.skipif(not _DATA_PATH.exists(), reason="hos_10d_canonical.json not present")
def test_cesar_garza_scenario() -> None:
    """Cesar Garza b382: 45h OFF 7/25–7/26 should reset weekly clock."""
    grouped = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    logs = [DCWCanonicalHOSLog.model_validate(r) for r in grouped["b382"]]
    events = logs_to_timeline_events(logs)
    as_of = datetime(2026, 7, 26, 22, 11, 4, tzinfo=UTC)

    weekly = compute_weekly_duty_seconds(
        events,
        as_of=as_of,
        cycle_days=settings.WEEKLY_CYCLE_DAYS,
        home_terminal_tz=CHICAGO,
    )
    assert weekly < settings.WEEKLY_CYCLE_LIMIT_HOURS * 3600

    timeline = DriverTimeline(driver_id="b382", tenant_id="tenant", events=events)
    pack = RulePack(version=settings.DEFAULT_RULE_PACK_VERSION)
    result = pack.evaluate(
        timeline,
        inputs_hash=compute_inputs_hash({"driver_id": "b382", "as_of": as_of.isoformat()}),
        weekly_duty_seconds=weekly,
        as_of=as_of,
    )
    weekly_violations = [v for v in result.violations if v.violation_type == ViolationType.WEEKLY_CYCLE]
    assert not weekly_violations

    reset = find_restart_reset_point(events, as_of, home_terminal_tz=CHICAGO)
    assert reset is not None
    assert reset <= as_of
