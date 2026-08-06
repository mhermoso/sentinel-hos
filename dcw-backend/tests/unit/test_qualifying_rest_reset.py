"""Regression: qualifying terminal rest must clear stale shift clocks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline, ViolationType
from app.domains.engine.state_machine import run_state_machine
from app.domains.engine.replay import truncate_timeline_to
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = timezone.utc


def _ts(hours: float) -> datetime:
    return datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _violating_shift_then_rest() -> DriverTimeline:
    """10h OFF → 12h DRIVING (over 11h) → OFF (open)."""
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
            timestamp=_ts(22),
        ),
    ]
    return DriverTimeline(driver_id="d1", tenant_id="t1", events=events)


def test_completed_qualifying_rest_clears_stale_driving_violation() -> None:
    """After 10h+ OFF following an 11h overage, point-in-time eval must reset."""
    timeline = _violating_shift_then_rest()
    pack = RulePack()

    still_violating = pack.evaluate(
        timeline,
        inputs_hash="mid-rest",
        as_of=_ts(22 + 5),  # only 5h into terminal OFF
    )
    assert any(
        v.violation_type == ViolationType.DRIVING_LIMIT for v in still_violating.violations
    )

    after_reset = pack.evaluate(
        timeline,
        inputs_hash="post-rest",
        as_of=_ts(22 + 10),  # qualifying 10h rest completed
    )
    assert after_reset.is_compliant is True
    assert after_reset.driving_remaining_seconds == pytest.approx(11 * 3600.0)
    assert after_reset.duty_window_remaining_seconds == pytest.approx(14 * 3600.0)
    assert after_reset.break_required is False
    assert not any(
        v.violation_type == ViolationType.DRIVING_LIMIT for v in after_reset.violations
    )


def test_state_machine_closes_shift_on_terminal_qualifying_rest() -> None:
    timeline = _violating_shift_then_rest()
    truncated = truncate_timeline_to(timeline, _ts(22 + 11))
    state = run_state_machine(truncated)

    assert state.consecutive_rest_seconds == pytest.approx(11 * 3600.0)
    assert state.current_shift is None
    assert state.driving_since_break_seconds == 0.0
    assert state.duty_window_elapsed_seconds == 0.0
    assert len(state.shifts) == 1
    assert state.shifts[0].cumulative_driving_seconds == pytest.approx(12 * 3600.0)


def test_resume_after_qualifying_rest_opens_fresh_shift() -> None:
    """Regression guard: next non-rest after 10h OFF still opens a new shift."""
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
            timestamp=_ts(18),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(28),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(30),
        ),
    ]
    timeline = DriverTimeline(driver_id="d1", tenant_id="t1", events=events)
    state = run_state_machine(timeline)

    assert state.current_shift is not None
    assert state.current_shift.shift_start == _ts(28)
    assert state.current_shift.cumulative_driving_seconds == pytest.approx(2 * 3600.0)
    assert len(state.shifts) == 1
