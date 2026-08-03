"""Phase 2 severity tests: PDF §8.3 thresholds on existing enums."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.domains.engine.calculators import (
    CRITICAL_OVERAGE_SECONDS,
    MAX_DRIVING_BEFORE_BREAK_SECONDS,
    MAX_DRIVING_SECONDS,
    MAX_DUTY_WINDOW_SECONDS,
    WARNING_THRESHOLD_SECONDS,
    WEEKLY_WARNING_USED_FRACTION,
    check_driving_limit,
    check_duty_window,
    check_rest_break,
    check_weekly_cycle,
)
from app.domains.engine.schemas import ShiftWindow, ViolationSeverity, ViolationType
from app.domains.engine.state_machine import StateMachineResult

UTC = timezone.utc


def _now() -> datetime:
    return datetime(2026, 3, 1, 20, 0, 0, tzinfo=UTC)


def _state(
    *,
    driven: float = 0.0,
    duty_elapsed: float = 0.0,
    driving_since_break: float = 0.0,
    driving: bool = True,
) -> StateMachineResult:
    shift_start = _now() - timedelta(seconds=max(driven, duty_elapsed, 1.0))
    return StateMachineResult(
        current_shift=ShiftWindow(
            shift_start=shift_start,
            qualifying_rest_before=shift_start - timedelta(hours=10),
            cumulative_driving_seconds=driven,
            cumulative_duty_seconds=duty_elapsed,
            driving_since_break_seconds=driving_since_break,
        ),
        duty_window_elapsed_seconds=duty_elapsed,
        driving_since_break_seconds=driving_since_break,
        is_currently_driving=driving,
    )


def test_warning_threshold_is_60_minutes() -> None:
    assert WARNING_THRESHOLD_SECONDS == 3600.0
    assert CRITICAL_OVERAGE_SECONDS == 15 * 60.0
    assert WEEKLY_WARNING_USED_FRACTION == 0.90


def test_driving_limit_warning_at_45_min_remaining() -> None:
    """Within 60 min of 11h → WARNING (would not fire under old 30 min threshold)."""
    driven = MAX_DRIVING_SECONDS - 45 * 60
    _, violations = check_driving_limit(_state(driven=driven), _now())
    assert len(violations) == 1
    assert violations[0].violation_type == ViolationType.DRIVING_LIMIT
    assert violations[0].severity == ViolationSeverity.WARNING


def test_driving_limit_no_warning_beyond_60_min() -> None:
    driven = MAX_DRIVING_SECONDS - 61 * 60
    _, violations = check_driving_limit(_state(driven=driven), _now())
    assert violations == []


def test_driving_limit_violation_at_limit_and_small_overage() -> None:
    _, at_limit = check_driving_limit(_state(driven=MAX_DRIVING_SECONDS), _now())
    assert at_limit[0].severity == ViolationSeverity.VIOLATION
    assert at_limit[0].overage_seconds == pytest.approx(0.0)

    _, small = check_driving_limit(
        _state(driven=MAX_DRIVING_SECONDS + CRITICAL_OVERAGE_SECONDS),
        _now(),
    )
    assert small[0].severity == ViolationSeverity.VIOLATION
    assert small[0].overage_seconds == pytest.approx(CRITICAL_OVERAGE_SECONDS)


def test_driving_limit_critical_when_overage_over_15_min() -> None:
    overage = CRITICAL_OVERAGE_SECONDS + 1
    _, violations = check_driving_limit(
        _state(driven=MAX_DRIVING_SECONDS + overage),
        _now(),
    )
    assert len(violations) == 1
    assert violations[0].severity == ViolationSeverity.CRITICAL
    assert violations[0].overage_seconds == pytest.approx(overage)


def test_duty_window_warning_within_60_min_while_driving() -> None:
    elapsed = MAX_DUTY_WINDOW_SECONDS - 50 * 60
    _, violations = check_duty_window(
        _state(duty_elapsed=elapsed, driving=True),
        _now(),
    )
    assert len(violations) == 1
    assert violations[0].severity == ViolationSeverity.WARNING


def test_duty_window_critical_over_15_min_overage() -> None:
    elapsed = MAX_DUTY_WINDOW_SECONDS + CRITICAL_OVERAGE_SECONDS + 60
    _, violations = check_duty_window(
        _state(duty_elapsed=elapsed, driving=True),
        _now(),
    )
    assert violations[0].severity == ViolationSeverity.CRITICAL


def test_duty_window_violation_at_or_under_15_min_overage() -> None:
    elapsed = MAX_DUTY_WINDOW_SECONDS + 10 * 60
    _, violations = check_duty_window(
        _state(duty_elapsed=elapsed, driving=True),
        _now(),
    )
    assert violations[0].severity == ViolationSeverity.VIOLATION


def test_rest_break_warning_within_60_min_of_8h() -> None:
    driving_since = MAX_DRIVING_BEFORE_BREAK_SECONDS - 45 * 60
    _, violations = check_rest_break(
        _state(driving_since_break=driving_since, driving=True),
        _now(),
    )
    assert len(violations) == 1
    assert violations[0].severity == ViolationSeverity.WARNING


def test_rest_break_missed_stays_violation_even_with_large_overage() -> None:
    driving_since = MAX_DRIVING_BEFORE_BREAK_SECONDS + 30 * 60
    _, violations = check_rest_break(
        _state(driving_since_break=driving_since, driving=True),
        _now(),
    )
    assert len(violations) == 1
    assert violations[0].severity == ViolationSeverity.VIOLATION


def test_weekly_warning_when_used_over_90_percent() -> None:
    limit = settings.WEEKLY_CYCLE_LIMIT_HOURS * 3600.0
    # Just over 90% used (remaining just under 10%)
    used = limit * WEEKLY_WARNING_USED_FRACTION + 1.0
    hours_used, hours_remaining, violations = check_weekly_cycle(used, _now())
    assert hours_used > settings.WEEKLY_CYCLE_LIMIT_HOURS * WEEKLY_WARNING_USED_FRACTION
    assert hours_remaining > 0
    assert len(violations) == 1
    assert violations[0].severity == ViolationSeverity.WARNING
    assert violations[0].violation_type == ViolationType.WEEKLY_CYCLE


def test_weekly_warning_at_exactly_90_percent_used() -> None:
    """remaining ≤10% of limit (used ≥90%) triggers WARNING."""
    limit = settings.WEEKLY_CYCLE_LIMIT_HOURS * 3600.0
    used = limit * WEEKLY_WARNING_USED_FRACTION
    _, _, violations = check_weekly_cycle(used, _now())
    assert len(violations) == 1
    assert violations[0].severity == ViolationSeverity.WARNING


def test_weekly_no_warning_below_90_percent() -> None:
    limit = settings.WEEKLY_CYCLE_LIMIT_HOURS * 3600.0
    used_under = limit * WEEKLY_WARNING_USED_FRACTION - 1.0
    _, _, under = check_weekly_cycle(used_under, _now())
    assert under == []


def test_weekly_exceeded_stays_violation() -> None:
    limit = settings.WEEKLY_CYCLE_LIMIT_HOURS * 3600.0
    _, _, violations = check_weekly_cycle(limit + 3600.0, _now())
    assert len(violations) == 1
    assert violations[0].severity == ViolationSeverity.VIOLATION
