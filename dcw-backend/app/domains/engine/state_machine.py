"""49 CFR Part 395 State Machine.

Processes a driver's ordered HOS event timeline and computes:
- Shift boundaries (start/end of duty after qualifying off-duty)
- Cumulative driving time within the shift
- 14-hour duty window elapsed time (wall-clock from first duty)
- Driving-since-last-break accumulator
- Split sleeper berth pairing (7+3 / 8+2) with 14h exclusion and rematch

All calculations are pure deterministic Python — zero probabilistic
or LLM scoring involved (per ADR-004).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.domains.engine.replay import RESTART_SECONDS, is_valid_restart_period
from app.domains.engine.schemas import (
    DriverTimeline,
    ShiftWindow,
)
from app.domains.ingestion.schemas import CanonicalDutyStatus

logger = logging.getLogger("dcw.engine.state_machine")

# ── Constants ─────────────────────────────────────────────────────────────

# Qualifying off-duty rest to start a new shift (10 consecutive hours)
QUALIFYING_OFF_DUTY_SECONDS: float = 10 * 3600.0

# 30-minute break reset threshold (any consecutive non-driving)
BREAK_RESET_SECONDS: float = 1800.0

# Split sleeper pairing thresholds (§ 395.1(g)(1) / PDF §3.4)
SPLIT_TOTAL_MIN_SECONDS: float = 10 * 3600.0
SPLIT_LONG_7_SECONDS: float = 7 * 3600.0
SPLIT_SHORT_3_SECONDS: float = 3 * 3600.0
SPLIT_LONG_8_SECONDS: float = 8 * 3600.0
SPLIT_SHORT_2_SECONDS: float = 2 * 3600.0

# Off-duty statuses that count toward rest (OFF, SB, or personal conveyance)
_REST_STATUSES = {
    CanonicalDutyStatus.OFF_DUTY,
    CanonicalDutyStatus.SLEEPER_BERTH,
    CanonicalDutyStatus.PERSONAL_CONVEYANCE,
}

# On-duty statuses that count against the 14h window and weekly cycle
_DUTY_STATUSES = {
    CanonicalDutyStatus.ON_DUTY,
    CanonicalDutyStatus.DRIVING,
    CanonicalDutyStatus.YARD_MOVE,
}

# Statuses that count as driving for the 11h and 30-min break rules
_DRIVING_STATUSES = {
    CanonicalDutyStatus.DRIVING,
}


@dataclass
class _Segment:
    """One closed status segment within the current shift."""

    start: datetime
    end: datetime
    status: CanonicalDutyStatus

    @property
    def duration(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def is_driving(self) -> bool:
        return self.status in _DRIVING_STATUSES

    @property
    def is_rest(self) -> bool:
        return self.status in _REST_STATUSES

    @property
    def is_duty(self) -> bool:
        return self.status in _DUTY_STATUSES


@dataclass
class _RestPeriod:
    """A consecutive rest block closed by returning to non-rest."""

    start: datetime
    end: datetime
    duration: float
    max_consecutive_sb: float
    paired: bool = False


@dataclass
class StateMachineResult:
    """Intermediate state produced by the state machine before rule evaluation."""

    current_shift: ShiftWindow | None = None
    shifts: list[ShiftWindow] = field(default_factory=list)
    total_driving_seconds: float = 0.0
    total_duty_seconds: float = 0.0
    duty_window_start: datetime | None = None
    duty_window_elapsed_seconds: float = 0.0
    driving_since_break_seconds: float = 0.0
    consecutive_rest_seconds: float = 0.0
    last_qualifying_rest_end: datetime | None = None
    had_34h_restart: bool = False
    last_valid_restart_at: datetime | None = None
    current_status: str | None = None
    is_currently_driving: bool = False

    # Split sleeper tracking
    pending_sb_block: tuple[datetime, float] | None = None  # legacy compat
    split_sleeper_active: bool = False
    split_excluded_intervals: list[tuple[datetime, datetime]] = field(default_factory=list)


def build_timeline_from_logs(
    logs: list[DriverTimeline.HOSEvent],
) -> list[DriverTimeline.HOSEvent]:
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


def _is_valid_split_pair(a: _RestPeriod, b: _RestPeriod) -> bool:
    """Return True when two rest periods form a 7+3 or 8+2 split-sleeper pair."""
    if a.duration + b.duration < SPLIT_TOTAL_MIN_SECONDS:
        return False

    def long7(p: _RestPeriod) -> bool:
        return p.max_consecutive_sb >= SPLIT_LONG_7_SECONDS

    def long8(p: _RestPeriod) -> bool:
        return p.max_consecutive_sb >= SPLIT_LONG_8_SECONDS

    def short3(p: _RestPeriod) -> bool:
        return p.duration >= SPLIT_SHORT_3_SECONDS

    def short2(p: _RestPeriod) -> bool:
        return p.duration >= SPLIT_SHORT_2_SECONDS

    # 7+3 (either order)
    if (long7(a) and short3(b)) or (long7(b) and short3(a)):
        return True
    # 8+2 (either order)
    if (long8(a) and short2(b)) or (long8(b) and short2(a)):
        return True
    return False


def _excluded_seconds(
    window_start: datetime,
    as_of: datetime,
    intervals: Sequence[tuple[datetime, datetime]],
) -> float:
    """Sum overlap of excluded intervals with [window_start, as_of]."""
    total = 0.0
    for start, end in intervals:
        lo = max(start, window_start)
        hi = min(end, as_of)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


def _recompute_clocks_from_segments(
    segments: Sequence[_Segment],
    *,
    rematch_anchor: datetime,
    excluded: Sequence[tuple[datetime, datetime]],
) -> tuple[float, float, float]:
    """Recompute duty seconds, driving, and break accumulator after rematch.

    Returns:
        (cumulative_duty_seconds, cumulative_driving_seconds, driving_since_break)
    """
    duty = 0.0
    driving = 0.0
    since_break = 0.0
    non_driving = 0.0

    for seg in segments:
        if seg.end <= rematch_anchor:
            continue
        start = max(seg.start, rematch_anchor)
        end = seg.end
        # Subtract excluded overlap from this segment for duty/driving clocks
        usable = (end - start).total_seconds()
        for ex_start, ex_end in excluded:
            lo = max(start, ex_start)
            hi = min(end, ex_end)
            if hi > lo:
                usable -= (hi - lo).total_seconds()
        usable = max(0.0, usable)

        # Break reset uses raw non-driving time (including excluded rest)
        raw_duration = max(0.0, (end - start).total_seconds())
        status = seg.status
        is_driving = status in _DRIVING_STATUSES

        if is_driving:
            if non_driving >= BREAK_RESET_SECONDS:
                since_break = 0.0
            non_driving = 0.0
            # Only count non-excluded driving toward 11h / break
            drive_part = usable
            driving += drive_part
            since_break += drive_part
            duty += drive_part
        else:
            non_driving += raw_duration
            if non_driving >= BREAK_RESET_SECONDS:
                since_break = 0.0
            if status in _DUTY_STATUSES:
                duty += usable

    return duty, driving, since_break


def run_state_machine(timeline: DriverTimeline) -> StateMachineResult:
    """Process driver timeline and return computed intermediate state.

    Handles:
    - 10-consecutive-hour off-duty qualifying rest detection
    - 34-hour restart detection
    - Split sleeper berth pairing (7+3 / 8+2) with 14h exclusion + rematch
    - Wall-clock 14h duty window from first ON/D/YM after reset
    - Break reset on any consecutive non-driving ≥ 30 minutes
    """
    result = StateMachineResult()

    if not timeline.events:
        return result

    events = build_timeline_from_logs(list(timeline.events))
    as_of = events[-1].timestamp

    consecutive_rest_start: datetime | None = None
    consecutive_rest_seconds: float = 0.0
    consecutive_sb_seconds: float = 0.0
    max_consecutive_sb_in_rest: float = 0.0
    rest_block_start: datetime | None = None

    consecutive_non_driving_seconds: float = 0.0

    shift_segments: list[_Segment] = []
    pending_rest_periods: list[_RestPeriod] = []
    excluded_intervals: list[tuple[datetime, datetime]] = []
    rematch_anchor: datetime | None = None

    def _close_rest_period(end_ts: datetime) -> None:
        """Close the open rest block and attempt split-sleeper pairing (look-back)."""
        nonlocal rematch_anchor, excluded_intervals, pending_rest_periods
        nonlocal shift_segments, consecutive_rest_start, consecutive_rest_seconds
        nonlocal consecutive_sb_seconds, max_consecutive_sb_in_rest, rest_block_start

        if (
            result.current_shift is None
            or rest_block_start is None
            or consecutive_rest_seconds <= 0
        ):
            return

        # Qualifying ≥10h rest starts a new shift — not a split period on the old one
        if consecutive_rest_seconds >= QUALIFYING_OFF_DUTY_SECONDS:
            return

        period = _RestPeriod(
            start=rest_block_start,
            end=end_ts,
            duration=consecutive_rest_seconds,
            max_consecutive_sb=max_consecutive_sb_in_rest,
        )

        # Look-back: try to pair with an earlier unpaired period
        paired_with: _RestPeriod | None = None
        for prior in pending_rest_periods:
            if prior.paired:
                continue
            if _is_valid_split_pair(prior, period):
                paired_with = prior
                break

        if paired_with is not None:
            paired_with.paired = True
            period.paired = True
            first, second = sorted(
                (paired_with, period),
                key=lambda p: p.start,
            )
            result.split_sleeper_active = True
            rematch_anchor = first.end
            result.duty_window_start = first.end
            # Exclude both qualifying periods from the 14h wall-clock
            excluded_intervals = [
                (first.start, first.end),
                (second.start, second.end),
            ]
            result.split_excluded_intervals = list(excluded_intervals)
            result.pending_sb_block = None

            duty, driving, since_break = _recompute_clocks_from_segments(
                shift_segments,
                rematch_anchor=rematch_anchor,
                excluded=excluded_intervals,
            )
            result.current_shift.cumulative_duty_seconds = duty
            result.current_shift.cumulative_driving_seconds = driving
            result.current_shift.driving_since_break_seconds = since_break
            result.driving_since_break_seconds = since_break
            result.total_driving_seconds = driving
        else:
            pending_rest_periods.append(period)
            if period.max_consecutive_sb >= SPLIT_SHORT_2_SECONDS:
                result.pending_sb_block = (period.start, period.duration)

    for event in events:
        status = CanonicalDutyStatus(event.status)
        duration = event.duration_seconds
        seg_start = event.timestamp
        seg_end = event.timestamp + timedelta(seconds=duration)

        result.current_status = event.status
        result.is_currently_driving = status in _DRIVING_STATUSES

        is_rest = status in _REST_STATUSES
        is_driving = status in _DRIVING_STATUSES
        is_duty = status in _DUTY_STATUSES

        # ── Track consecutive rest ────────────────────────────────────
        if is_rest:
            if consecutive_rest_start is None:
                consecutive_rest_start = event.timestamp
                rest_block_start = event.timestamp
                max_consecutive_sb_in_rest = 0.0
                consecutive_sb_seconds = 0.0
            consecutive_rest_seconds += duration
            if status == CanonicalDutyStatus.SLEEPER_BERTH:
                consecutive_sb_seconds += duration
                max_consecutive_sb_in_rest = max(
                    max_consecutive_sb_in_rest, consecutive_sb_seconds
                )
            else:
                consecutive_sb_seconds = 0.0
        else:
            # Non-rest: check if accumulated rest qualifies to start a shift
            if consecutive_rest_seconds >= QUALIFYING_OFF_DUTY_SECONDS:
                if result.current_shift is not None:
                    result.shifts.append(result.current_shift)
                result.current_shift = ShiftWindow(
                    shift_start=event.timestamp,
                    qualifying_rest_before=consecutive_rest_start or event.timestamp,
                )
                result.driving_since_break_seconds = 0.0
                result.duty_window_elapsed_seconds = 0.0
                result.duty_window_start = None
                result.split_sleeper_active = False
                result.pending_sb_block = None
                result.split_excluded_intervals = []
                shift_segments = []
                pending_rest_periods = []
                excluded_intervals = []
                rematch_anchor = None
                consecutive_non_driving_seconds = 0.0

                if consecutive_rest_seconds >= RESTART_SECONDS:
                    rest_start = consecutive_rest_start or event.timestamp
                    if is_valid_restart_period(rest_start, event.timestamp):
                        result.had_34h_restart = True
                        result.last_valid_restart_at = event.timestamp

            elif consecutive_rest_seconds > 0 and result.current_shift is not None:
                # Close sub-10h rest block for possible split pairing
                _close_rest_period(event.timestamp)

            consecutive_rest_start = None
            consecutive_rest_seconds = 0.0
            consecutive_sb_seconds = 0.0
            max_consecutive_sb_in_rest = 0.0
            rest_block_start = None

        # ── Record segment + accumulate within current shift ──────────
        if result.current_shift is not None and duration > 0:
            shift_segments.append(_Segment(start=seg_start, end=seg_end, status=status))

        if result.current_shift is not None:
            # First ON/D/YM after 10h reset starts the 14h wall-clock
            if is_duty and result.duty_window_start is None:
                result.duty_window_start = event.timestamp

            if is_driving:
                if consecutive_non_driving_seconds >= BREAK_RESET_SECONDS:
                    result.current_shift.driving_since_break_seconds = 0.0
                    result.driving_since_break_seconds = 0.0
                consecutive_non_driving_seconds = 0.0

                # Only accumulate when not inside an excluded split interval
                # (rematch path recomputes; live path adds incrementally until rematch)
                if rematch_anchor is None:
                    result.current_shift.cumulative_driving_seconds += duration
                    result.total_driving_seconds += duration
                    result.current_shift.driving_since_break_seconds += duration
                    result.driving_since_break_seconds += duration
                    if is_duty:
                        result.current_shift.cumulative_duty_seconds += duration
                # After rematch, recompute from segments at each step for accuracy
                else:
                    duty, driving, since_break = _recompute_clocks_from_segments(
                        shift_segments,
                        rematch_anchor=rematch_anchor,
                        excluded=excluded_intervals,
                    )
                    result.current_shift.cumulative_duty_seconds = duty
                    result.current_shift.cumulative_driving_seconds = driving
                    result.current_shift.driving_since_break_seconds = since_break
                    result.driving_since_break_seconds = since_break
                    result.total_driving_seconds = driving
            else:
                consecutive_non_driving_seconds += duration
                if consecutive_non_driving_seconds >= BREAK_RESET_SECONDS:
                    result.current_shift.driving_since_break_seconds = 0.0
                    result.driving_since_break_seconds = 0.0

                if rematch_anchor is None and is_duty:
                    result.current_shift.cumulative_duty_seconds += duration
                elif rematch_anchor is not None:
                    duty, driving, since_break = _recompute_clocks_from_segments(
                        shift_segments,
                        rematch_anchor=rematch_anchor,
                        excluded=excluded_intervals,
                    )
                    result.current_shift.cumulative_duty_seconds = duty
                    result.current_shift.cumulative_driving_seconds = driving
                    result.current_shift.driving_since_break_seconds = since_break
                    result.driving_since_break_seconds = since_break
                    result.total_driving_seconds = driving

    # Finalise last shift — wall-clock 14h elapsed
    if result.current_shift is not None:
        result.total_duty_seconds = result.current_shift.cumulative_duty_seconds
        if result.duty_window_start is not None:
            raw = max(0.0, (as_of - result.duty_window_start).total_seconds())
            excluded = _excluded_seconds(
                result.duty_window_start, as_of, excluded_intervals
            )
            result.duty_window_elapsed_seconds = max(0.0, raw - excluded)
        result.split_excluded_intervals = list(excluded_intervals)

    result.consecutive_rest_seconds = consecutive_rest_seconds
    return result
