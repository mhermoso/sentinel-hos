"""Phase 1 clock tests: wall-clock 14h, break reset, split sleeper pairing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domains.engine.calculators import (
    MAX_DUTY_WINDOW_SECONDS,
    check_duty_window,
    check_rest_break,
)
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline, ViolationSeverity, ViolationType
from app.domains.engine.state_machine import run_state_machine
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = UTC


def _ts(hours: float) -> datetime:
    return datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _evt(status: CanonicalDutyStatus, hours: float) -> DriverTimeline.HOSEvent:
    return DriverTimeline.HOSEvent(status=status.value, timestamp=_ts(hours))


def _timeline(*events: DriverTimeline.HOSEvent) -> DriverTimeline:
    return DriverTimeline(driver_id="d1", tenant_id="t1", events=list(events))


# ── 14h wall-clock ────────────────────────────────────────────────────────


def test_duty_window_is_wall_clock_not_summed_duty() -> None:
    """OFF inside the shift does not pause the 14h wall-clock."""
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),  # shift starts
        _evt(CanonicalDutyStatus.OFF_DUTY, 12),  # 2h drive, then OFF
        _evt(CanonicalDutyStatus.DRIVING, 16),  # 4h OFF (wall-clock continues)
        _evt(CanonicalDutyStatus.OFF_DUTY, 18),
    ]
    result = run_state_machine(_timeline(*events))

    assert result.duty_window_start == _ts(10)
    # Wall-clock from first duty (t=10) to last event (t=18) = 8h
    assert result.duty_window_elapsed_seconds == pytest.approx(8 * 3600.0)
    # Only 4h of actual driving
    assert result.current_shift is not None
    assert result.current_shift.cumulative_driving_seconds == pytest.approx(4 * 3600.0)


def test_duty_window_violation_only_while_driving() -> None:
    """Elapsed ≥14h while ON_DUTY (not driving) is not a DUTY_WINDOW violation."""
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.ON_DUTY, 10),
        _evt(CanonicalDutyStatus.ON_DUTY, 24),  # 14h wall-clock elapsed, still ON
    ]
    # Truncate-style close: last event at t=24 with prior ON from t=10 → 14h
    pack = RulePack()
    result = pack.evaluate(
        _timeline(*events[:-1]),
        inputs_hash="h",
        as_of=_ts(24),
    )
    duty_violations = [v for v in result.violations if v.violation_type == ViolationType.DUTY_WINDOW]
    assert duty_violations == []

    # Same elapsed while Driving → violation
    driving_timeline = _timeline(
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
    )
    # 14h + 10 min overage → VIOLATION (SERIOUS); >15 min would be CRITICAL
    late = pack.evaluate(driving_timeline, inputs_hash="h", as_of=_ts(24 + 10 / 60))
    assert any(
        v.violation_type == ViolationType.DUTY_WINDOW and v.severity == ViolationSeverity.VIOLATION
        for v in late.violations
    )


def test_duty_window_warning_only_while_driving() -> None:
    state = run_state_machine(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.ON_DUTY, 10),
            _evt(CanonicalDutyStatus.ON_DUTY, 23.75),  # 13.75h elapsed, ON not driving
        )
    )
    # Force elapsed near limit
    state.duty_window_elapsed_seconds = MAX_DUTY_WINDOW_SECONDS - 900
    state.is_currently_driving = False
    _, violations = check_duty_window(state, _ts(23.75))
    assert violations == []

    state.is_currently_driving = True
    _, violations = check_duty_window(state, _ts(23.75))
    assert len(violations) == 1
    assert violations[0].severity == ViolationSeverity.WARNING


# ── Break reset ───────────────────────────────────────────────────────────


def test_break_resets_on_on_duty_non_driving_30_min() -> None:
    """ON_DUTY ≥30 min resets driving_since_break (not only OFF/SB)."""
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
        _evt(CanonicalDutyStatus.ON_DUTY, 17),  # 7h driving
        _evt(CanonicalDutyStatus.DRIVING, 17.5),  # 30 min ON → reset
        _evt(CanonicalDutyStatus.OFF_DUTY, 19),  # 1.5h more driving
    ]
    result = run_state_machine(_timeline(*events))
    assert result.driving_since_break_seconds == pytest.approx(1.5 * 3600.0)


def test_rest_break_violation_only_while_driving() -> None:
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
        _evt(CanonicalDutyStatus.ON_DUTY, 18.5),  # 8.5h driving then ON
    ]
    state = run_state_machine(_timeline(*events))
    assert state.driving_since_break_seconds >= 8 * 3600.0
    assert state.is_currently_driving is False
    required, violations = check_rest_break(state, _ts(18.5))
    assert required is True
    assert violations == []

    state.is_currently_driving = True
    required, violations = check_rest_break(state, _ts(18.5))
    assert required is True
    assert len(violations) == 1
    assert violations[0].violation_type == ViolationType.REST_BREAK


# ── Split sleeper ─────────────────────────────────────────────────────────


def test_split_sleeper_7_plus_3_excludes_from_14h_and_rematches() -> None:
    """7h SB + 3h OFF: exclude both from 14h; rematch clocks from end of first period."""
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),  # 2h drive before first berth
        _evt(CanonicalDutyStatus.SLEEPER_BERTH, 12),  # 7h SB
        _evt(CanonicalDutyStatus.DRIVING, 19),  # 4h drive between
        _evt(CanonicalDutyStatus.OFF_DUTY, 23),  # 3h OFF (completes 7+3)
        _evt(CanonicalDutyStatus.DRIVING, 26),  # resume driving
        _evt(CanonicalDutyStatus.OFF_DUTY, 28),
    ]
    result = run_state_machine(_timeline(*events))

    assert result.split_sleeper_active is True
    # Rematch from end of first period (SB ends at t=19)
    assert result.duty_window_start == _ts(19)
    # Wall-clock from t=19 to t=28 = 9h, minus 3h OFF exclusion = 6h
    assert result.duty_window_elapsed_seconds == pytest.approx(6 * 3600.0)
    # Driving after rematch: 4h between + 2h after = 6h (pre-berth 2h wiped)
    assert result.current_shift is not None
    assert result.current_shift.cumulative_driving_seconds == pytest.approx(6 * 3600.0)


def test_split_sleeper_8_plus_2() -> None:
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
        _evt(CanonicalDutyStatus.SLEEPER_BERTH, 11),  # 8h SB
        _evt(CanonicalDutyStatus.DRIVING, 19),  # 3h between
        _evt(CanonicalDutyStatus.OFF_DUTY, 22),  # 2h OFF
        _evt(CanonicalDutyStatus.DRIVING, 24),
        _evt(CanonicalDutyStatus.OFF_DUTY, 25),
    ]
    result = run_state_machine(_timeline(*events))
    assert result.split_sleeper_active is True
    assert result.duty_window_start == _ts(19)
    # From 19→25 = 6h wall, minus 2h OFF = 4h
    assert result.duty_window_elapsed_seconds == pytest.approx(4 * 3600.0)
    assert result.current_shift is not None
    assert result.current_shift.cumulative_driving_seconds == pytest.approx(4 * 3600.0)


def test_split_sleeper_invalid_pairing_no_rematch() -> None:
    """6h SB + 4h OFF (total 10h) is not a valid 7+3/8+2 pair."""
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
        _evt(CanonicalDutyStatus.SLEEPER_BERTH, 12),  # 6h SB — too short for long berth
        _evt(CanonicalDutyStatus.DRIVING, 18),
        _evt(CanonicalDutyStatus.OFF_DUTY, 20),  # 4h OFF
        _evt(CanonicalDutyStatus.DRIVING, 24),
        _evt(CanonicalDutyStatus.OFF_DUTY, 25),
    ]
    result = run_state_machine(_timeline(*events))
    assert result.split_sleeper_active is False
    assert result.duty_window_start == _ts(10)
    # Full wall-clock 10→25 with no exclusions
    assert result.duty_window_elapsed_seconds == pytest.approx(15 * 3600.0)


def test_split_sleeper_retrospective_short_then_long() -> None:
    """3h OFF first, then 7h SB — look-back pairs when long berth closes."""
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),  # 1h drive
        _evt(CanonicalDutyStatus.OFF_DUTY, 11),  # 3h OFF (pending short)
        _evt(CanonicalDutyStatus.DRIVING, 14),  # 2h between
        _evt(CanonicalDutyStatus.SLEEPER_BERTH, 16),  # 7h SB closes pair
        _evt(CanonicalDutyStatus.DRIVING, 23),
        _evt(CanonicalDutyStatus.OFF_DUTY, 24),
    ]
    result = run_state_machine(_timeline(*events))
    assert result.split_sleeper_active is True
    # First period is the 3h OFF ending at t=14
    assert result.duty_window_start == _ts(14)
    # Wall 14→24 = 10h minus 7h SB = 3h
    assert result.duty_window_elapsed_seconds == pytest.approx(3 * 3600.0)
    # Driving after rematch: 2h between + 1h after = 3h
    assert result.current_shift is not None
    assert result.current_shift.cumulative_driving_seconds == pytest.approx(3 * 3600.0)


def test_rule_pack_default_version_is_2_3_0() -> None:
    assert RulePack().version == "fmcsa-us-property@2.5.0"
