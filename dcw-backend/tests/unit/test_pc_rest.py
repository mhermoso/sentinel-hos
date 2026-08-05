"""Engine tests: personal conveyance counts as rest; yard-move as duty (not driving)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domains.engine.replay import compute_weekly_duty_seconds
from app.domains.engine.schemas import DriverTimeline
from app.domains.engine.state_machine import run_state_machine
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = UTC


def _ts(hours: float) -> datetime:
    return datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def test_personal_conveyance_counts_toward_qualifying_rest() -> None:
    """10h PC then driving should open a new shift (PC = off-duty rest)."""
    events = [
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
            timestamp=_ts(0),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(10),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(12),
        ),
    ]
    timeline = DriverTimeline(driver_id="d1", tenant_id="t1", events=events)
    result = run_state_machine(timeline)

    assert result.current_shift is not None
    assert result.current_shift.shift_start == _ts(10)
    assert result.current_shift.cumulative_driving_seconds == pytest.approx(2 * 3600.0)


def test_pc_does_not_count_toward_weekly_duty() -> None:
    events = [
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
            timestamp=_ts(0),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(4),
        ),
    ]
    seconds = compute_weekly_duty_seconds(events, as_of=_ts(4), cycle_days=8)
    assert seconds == pytest.approx(0.0)


def test_yard_move_counts_as_duty_not_driving() -> None:
    """YM after 10h OFF accumulates weekly/duty window but not the 11h driving clock."""
    events = [
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(0),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.YARD_MOVE.value,
            timestamp=_ts(10),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(12),
        ),
    ]
    timeline = DriverTimeline(driver_id="d1", tenant_id="t1", events=events)
    result = run_state_machine(timeline)

    assert result.current_shift is not None
    assert result.current_shift.cumulative_driving_seconds == pytest.approx(0.0)
    assert result.total_driving_seconds == pytest.approx(0.0)
    assert result.current_shift.cumulative_duty_seconds == pytest.approx(2 * 3600.0)
    assert result.duty_window_start == _ts(10)
    # Wall-clock from YM start to last event (OFF at +2h)
    assert result.duty_window_elapsed_seconds == pytest.approx(2 * 3600.0)

    weekly = compute_weekly_duty_seconds(events, as_of=_ts(12), cycle_days=8)
    assert weekly == pytest.approx(2 * 3600.0)
