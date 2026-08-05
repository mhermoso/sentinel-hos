"""30-min break must credit consecutive OFF/SB/PC, not a single event duration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.engine.replay import truncate_timeline_to
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline, ViolationSeverity, ViolationType
from app.domains.engine.state_machine import run_state_machine
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = timezone.utc


def _ts(hours: float) -> datetime:
    return datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _timeline_off_then_sb_break() -> DriverTimeline:
    """10h OFF, drive 7.5h, OFF 20m + SB 20m (40m consecutive rest), resume D."""
    break_start = _ts(17.5)
    resume = break_start + timedelta(minutes=40)
    events = [
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(0),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(10),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=break_start,
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.SLEEPER_BERTH.value,
            timestamp=break_start + timedelta(minutes=20),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=resume,
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=resume + timedelta(hours=1),
        ),
    ]
    return DriverTimeline(driver_id="d1", tenant_id="t1", events=events)


def test_split_off_sb_break_resets_driving_since_break() -> None:
    """OFF 20m + SB 20m is a valid 30-min break even though each segment is <30m."""
    timeline = _timeline_off_then_sb_break()
    as_of = _ts(17.5) + timedelta(minutes=40) + timedelta(hours=0.75)
    truncated = truncate_timeline_to(timeline, as_of)
    state = run_state_machine(truncated)

    assert state.driving_since_break_seconds == pytest.approx(0.75 * 3600.0)

    result = RulePack().evaluate(
        truncated,
        inputs_hash="test",
        weekly_duty_seconds=0.0,
        as_of=as_of,
    )
    rest_break = [
        v
        for v in result.violations
        if v.violation_type == ViolationType.REST_BREAK
        and v.severity in (ViolationSeverity.VIOLATION, ViolationSeverity.CRITICAL)
    ]
    assert rest_break == []


def test_single_short_rest_does_not_reset_break() -> None:
    """A single 20m OFF is not enough — break clock must continue."""
    events = [
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(0),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(10),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(17.5),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(17.5) + timedelta(minutes=20),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(17.5) + timedelta(minutes=20) + timedelta(hours=1),
        ),
    ]
    timeline = DriverTimeline(driver_id="d1", tenant_id="t1", events=events)
    as_of = _ts(17.5) + timedelta(minutes=20) + timedelta(hours=0.75)
    truncated = truncate_timeline_to(timeline, as_of)
    state = run_state_machine(truncated)

    # 7.5h before short rest + 0.75h after = 8.25h since break
    assert state.driving_since_break_seconds == pytest.approx(8.25 * 3600.0)
