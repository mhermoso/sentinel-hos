"""Regression tests for HOS status classification and shift bootstrap."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline, ViolationType
from app.domains.engine.state_machine import run_state_machine
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = timezone.utc


def _timeline(segments: list[tuple[str, float]]) -> tuple[DriverTimeline, datetime]:
    """Build a timeline from (status, duration_hours) segments ending at a fixed now."""
    now = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)
    cursor = now
    points: list[tuple[str, datetime]] = []
    for status, dur_h in reversed(segments):
        cursor = cursor - timedelta(hours=dur_h)
        points.append((status, cursor))
    points.reverse()
    events = [
        DriverTimeline.HOSEvent(status=status, timestamp=ts) for status, ts in points
    ]
    return DriverTimeline(driver_id="drv1", tenant_id="tenant1", events=events), now


def test_personal_conveyance_counts_toward_qualifying_rest() -> None:
    """9h OFF + 1h PC must complete a 10h rest and open a new driving shift."""
    timeline, as_of = _timeline(
        [
            (CanonicalDutyStatus.OFF_DUTY.value, 9),
            (CanonicalDutyStatus.PERSONAL_CONVEYANCE.value, 1),
            (CanonicalDutyStatus.DRIVING.value, 11),
        ]
    )
    result = RulePack().evaluate(timeline, inputs_hash="pc-rest", as_of=as_of)

    assert any(v.violation_type == ViolationType.DRIVING_LIMIT for v in result.violations)
    assert result.driving_remaining_seconds == pytest.approx(0.0)


def test_yard_move_is_duty_not_driving() -> None:
    """Yard Move must burn the 14h window but must not create an 11h driving violation."""
    from app.domains.engine.replay import truncate_timeline_to

    timeline, as_of = _timeline(
        [
            (CanonicalDutyStatus.OFF_DUTY.value, 10),
            (CanonicalDutyStatus.YARD_MOVE.value, 12),
        ]
    )
    state = run_state_machine(truncate_timeline_to(timeline, as_of))
    result = RulePack().evaluate(timeline, inputs_hash="ym-duty", as_of=as_of)

    assert state.current_shift is not None
    assert state.current_shift.cumulative_driving_seconds == pytest.approx(0.0)
    assert state.current_shift.cumulative_duty_seconds == pytest.approx(12 * 3600.0)
    assert not any(v.violation_type == ViolationType.DRIVING_LIMIT for v in result.violations)
    assert not any(v.violation_type == ViolationType.REST_BREAK for v in result.violations)


def test_insufficient_rest_still_tracks_subsequent_driving() -> None:
    """8h OFF does not reset clocks; subsequent 11h driving must still be credited."""
    timeline, as_of = _timeline(
        [
            (CanonicalDutyStatus.OFF_DUTY.value, 8),
            (CanonicalDutyStatus.DRIVING.value, 11),
        ]
    )
    result = RulePack().evaluate(timeline, inputs_hash="short-rest", as_of=as_of)

    assert any(v.violation_type == ViolationType.DRIVING_LIMIT for v in result.violations)
    assert result.driving_remaining_seconds == pytest.approx(0.0)


def test_truncated_lookback_mid_drive_still_detects_11h_limit() -> None:
    """When lookback starts mid-shift with only driving events, still detect over-limit."""
    timeline, as_of = _timeline([(CanonicalDutyStatus.DRIVING.value, 11)])
    result = RulePack().evaluate(timeline, inputs_hash="mid-shift", as_of=as_of)

    assert any(v.violation_type == ViolationType.DRIVING_LIMIT for v in result.violations)
    assert result.driving_remaining_seconds == pytest.approx(0.0)


def test_on_duty_not_driving_resets_30_min_break_clock() -> None:
    """≥30 min on-duty-not-driving satisfies the rest-break requirement."""
    timeline, as_of = _timeline(
        [
            (CanonicalDutyStatus.OFF_DUTY.value, 10),
            (CanonicalDutyStatus.DRIVING.value, 8),
            (CanonicalDutyStatus.ON_DUTY.value, 0.5),
            (CanonicalDutyStatus.DRIVING.value, 1),
        ]
    )
    result = RulePack().evaluate(timeline, inputs_hash="onduty-break", as_of=as_of)

    assert not any(v.violation_type == ViolationType.REST_BREAK for v in result.violations)
