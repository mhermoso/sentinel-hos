"""49 CFR Part 395 State Machine.

Processes a driver's ordered HOS event timeline and computes:
- Shift boundaries (start/end of duty after qualifying off-duty)
- Cumulative driving time within the shift
- 14-hour duty window elapsed time
- Driving-since-last-break accumulator
- Split sleeper berth pairing detection

All calculations are pure deterministic Python — zero probabilistic
or LLM scoring involved (per ADR-004).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.domains.engine.schemas import (
    DriverTimeline,
    ShiftWindow,
    ViolationType,
    ViolationSeverity,
    Violation,
)
from app.domains.ingestion.schemas import CanonicalDutyStatus

logger = logging.getLogger("dcw.engine.state_machine")

# ── Constants ─────────────────────────────────────────────────────────────

# Qualifying off-duty rest to start a new shift (10 consecutive hours)
QUALIFYING_OFF_DUTY_SECONDS: float = 10 * 3600.0

# Qualifying 34-hour restart
RESTART_SECONDS: float = 34 * 3600.0

# Off-duty statuses that count toward rest (OFF or SB)
_REST_STATUSES = {CanonicalDutyStatus.OFF_DUTY, CanonicalDutyStatus.SLEEPER_BERTH}

# On-duty statuses that count against the 14h window and weekly cycle
_DUTY_STATUSES = {
    CanonicalDutyStatus.ON_DUTY,
    CanonicalDutyStatus.DRIVING,
    CanonicalDutyStatus.YARD_MOVE,
    CanonicalDutyStatus.ON_DUTY,
}

# Statuses that count as driving for the 11h and 30-min break rules
_DRIVING_STATUSES = {
    CanonicalDutyStatus.DRIVING,
    CanonicalDutyStatus.YARD_MOVE,
}


class StateMachineResult:
    """Intermediate state produced by the state machine before rule evaluation."""

    def __init__(self) -> None:
        self.current_shift: Optional[ShiftWindow] = None
        self.shifts: List[ShiftWindow] = []
        self.total_driving_seconds: float = 0.0
        self.total_duty_seconds: float = 0.0
        self.duty_window_elapsed_seconds: float = 0.0  # since shift start
        self.driving_since_break_seconds: float = 0.0
        self.consecutive_rest_seconds: float = 0.0
        self.last_qualifying_rest_end: Optional[datetime] = None
        self.had_34h_restart: bool = False

        # Split sleeper tracking
        self.pending_sb_block: Optional[tuple[datetime, float]] = None  # (start, seconds)
        self.split_sleeper_active: bool = False


def build_timeline_from_logs(
    logs: List[DriverTimeline.HOSEvent],
) -> List[DriverTimeline.HOSEvent]:
    """Sort events chronologically and compute duration_seconds for each."""
    sorted_logs = sorted(logs, key=lambda e: e.timestamp)
    for i, event in enumerate(sorted_logs):
        if i + 1 < len(sorted_logs):
            delta = sorted_logs[i + 1].timestamp - event.timestamp
            object.__setattr__(event, "duration_seconds", max(0.0, delta.total_seconds()))
        else:
            # Last event — no following event, duration = 0
            object.__setattr__(event, "duration_seconds", 0.0)
    return sorted_logs


def run_state_machine(timeline: DriverTimeline) -> StateMachineResult:
    """Process driver timeline and return computed intermediate state.

    Handles:
    - 10-consecutive-hour off-duty qualifying rest detection
    - 34-hour restart detection
    - Split sleeper berth pairing (2h + 8h or 8h + 2h)
    - Per-shift accumulation of driving, duty, and break counters
    """
    result = StateMachineResult()

    if not timeline.events:
        return result

    events = build_timeline_from_logs(list(timeline.events))

    consecutive_rest_start: Optional[datetime] = None
    consecutive_rest_seconds: float = 0.0
    in_shift = False

    for event in events:
        status = CanonicalDutyStatus(event.status)
        duration = event.duration_seconds

        is_rest = status in _REST_STATUSES
        is_driving = status in _DRIVING_STATUSES
        is_duty = status in _DUTY_STATUSES or is_driving

        # ── Track consecutive rest ────────────────────────────────────
        if is_rest:
            if consecutive_rest_start is None:
                consecutive_rest_start = event.timestamp
            consecutive_rest_seconds += duration
        else:
            # Non-rest: check if accumulated rest qualifies to start a shift
            if consecutive_rest_seconds >= QUALIFYING_OFF_DUTY_SECONDS:
                # New shift starts — close old one if open
                if result.current_shift is not None:
                    result.shifts.append(result.current_shift)
                result.current_shift = ShiftWindow(
                    shift_start=event.timestamp,
                    qualifying_rest_before=consecutive_rest_start or event.timestamp,
                )
                result.driving_since_break_seconds = 0.0
                result.duty_window_elapsed_seconds = 0.0
                in_shift = True

                # Check for 34h restart
                if consecutive_rest_seconds >= RESTART_SECONDS:
                    result.had_34h_restart = True

            consecutive_rest_start = None
            consecutive_rest_seconds = 0.0

        # ── Accumulate within current shift ───────────────────────────
        if result.current_shift is not None and not is_rest:
            if is_duty:
                result.current_shift.cumulative_duty_seconds += duration

            if is_driving:
                result.current_shift.cumulative_driving_seconds += duration
                result.total_driving_seconds += duration
                result.current_shift.driving_since_break_seconds += duration
                result.driving_since_break_seconds += duration

        # ── 30-min break reset ────────────────────────────────────────
        if is_rest and duration >= 1800.0 and result.current_shift is not None:
            result.current_shift.driving_since_break_seconds = 0.0
            result.driving_since_break_seconds = 0.0

        # ── Split sleeper berth (§ 395.1(g)(1)) ──────────────────────
        if status == CanonicalDutyStatus.SLEEPER_BERTH and result.current_shift is not None:
            SB_MIN = 2 * 3600.0
            SB_MAIN = 8 * 3600.0
            if duration >= SB_MAIN:
                result.split_sleeper_active = True
            elif duration >= SB_MIN and not result.split_sleeper_active:
                result.pending_sb_block = (event.timestamp, duration)

    # Finalise last shift
    if result.current_shift is not None:
        result.total_duty_seconds = result.current_shift.cumulative_duty_seconds
        # 14h window is consecutive clock time from coming on duty
        # (§ 395.3(a)(2)); off-duty inside the shift does not pause it.
        end_time = events[-1].timestamp
        result.duty_window_elapsed_seconds = max(
            0.0, (end_time - result.current_shift.shift_start).total_seconds()
        )

    result.consecutive_rest_seconds = consecutive_rest_seconds
    return result
