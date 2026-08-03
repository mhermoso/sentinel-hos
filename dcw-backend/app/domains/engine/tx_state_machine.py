"""Texas intrastate HOS state machine (37 TAC §4.12 / PDF §5).

Differences from federal Ruleset A:
- Qualifying reset is **8** consecutive hours OFF/SB/PC (not 10).
- Duty clock is **accumulated** ON + Driving + Yard Move (not wall-clock).
- No 30-minute break accumulator.
- 34h restart resets the weekly cycle **without** the federal 1–5 AM gate.
- Sleeper split §5.3: two SB periods each ≥2h totaling ≥8h, with rematch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from app.domains.engine.replay import RESTART_SECONDS
from app.domains.engine.schemas import DriverTimeline, ShiftWindow
from app.domains.engine.state_machine import build_timeline_from_logs
from app.domains.ingestion.schemas import CanonicalDutyStatus

logger = logging.getLogger("dcw.engine.tx_state_machine")

# ── Texas constants ────────────────────────────────────────────────────────

TX_QUALIFYING_OFF_DUTY_SECONDS: float = 8 * 3600.0
TX_SPLIT_MIN_PERIOD_SECONDS: float = 2 * 3600.0
TX_SPLIT_TOTAL_MIN_SECONDS: float = 8 * 3600.0
TX_MAX_DRIVING_SECONDS: float = 12 * 3600.0
TX_MAX_DUTY_SECONDS: float = 15 * 3600.0

_REST_STATUSES = {
    CanonicalDutyStatus.OFF_DUTY,
    CanonicalDutyStatus.SLEEPER_BERTH,
    CanonicalDutyStatus.PERSONAL_CONVEYANCE,
}

_DUTY_STATUSES = {
    CanonicalDutyStatus.ON_DUTY,
    CanonicalDutyStatus.DRIVING,
    CanonicalDutyStatus.YARD_MOVE,
}

_DRIVING_STATUSES = {
    CanonicalDutyStatus.DRIVING,
}


@dataclass
class _Segment:
    start: datetime
    end: datetime
    status: CanonicalDutyStatus

    @property
    def duration(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())


@dataclass
class _SbPeriod:
    """A closed sleeper-berth block eligible for Texas split pairing."""

    start: datetime
    end: datetime
    duration: float
    paired: bool = False


@dataclass
class TxStateMachineResult:
    """Intermediate TX clocks before rule evaluation."""

    current_shift: Optional[ShiftWindow] = None
    shifts: List[ShiftWindow] = field(default_factory=list)
    total_driving_seconds: float = 0.0
    total_duty_seconds: float = 0.0
    # Wall-clock tour start for Ruleset D 12h release (first ON/D/YM after 8h).
    duty_window_start: Optional[datetime] = None
    duty_window_elapsed_seconds: float = 0.0
    # Accumulated ON+D+YM since last 8h reset / split rematch.
    accumulated_duty_seconds: float = 0.0
    consecutive_rest_seconds: float = 0.0
    last_qualifying_rest_end: Optional[datetime] = None
    had_34h_restart: bool = False
    last_valid_restart_at: Optional[datetime] = None
    current_status: Optional[str] = None
    is_currently_driving: bool = False
    split_sleeper_active: bool = False
    split_excluded_intervals: List[Tuple[datetime, datetime]] = field(default_factory=list)


def _recompute_from_segments(
    segments: Sequence[_Segment],
    *,
    rematch_anchor: datetime,
) -> tuple[float, float]:
    """Recompute accumulated duty + driving after a Texas split rematch."""
    duty = 0.0
    driving = 0.0
    for seg in segments:
        if seg.end <= rematch_anchor:
            continue
        start = max(seg.start, rematch_anchor)
        usable = max(0.0, (seg.end - start).total_seconds())
        if seg.status in _DRIVING_STATUSES:
            driving += usable
            duty += usable
        elif seg.status in _DUTY_STATUSES:
            duty += usable
    return duty, driving


def _driving_around_period(
    segments: Sequence[_Segment],
    period: _SbPeriod,
    *,
    until: Optional[datetime] = None,
) -> float:
    """Driving immediately before + after a sleeper period (split validity)."""
    before = 0.0
    after = 0.0
    for seg in segments:
        if seg.status not in _DRIVING_STATUSES:
            continue
        # Before: ends at or before period start, contiguous look-back not required —
        # sum all driving in the shift before the period start.
        if seg.end <= period.start:
            before += seg.duration
        elif seg.start >= period.end:
            if until is not None and seg.start >= until:
                continue
            end = seg.end if until is None else min(seg.end, until)
            after += max(0.0, (end - seg.start).total_seconds())
    return before + after


def _duty_around_period(
    segments: Sequence[_Segment],
    period: _SbPeriod,
    *,
    until: Optional[datetime] = None,
) -> float:
    """Accumulated ON+D+YM immediately before + after a sleeper period."""
    total = 0.0
    for seg in segments:
        if seg.status not in _DUTY_STATUSES:
            continue
        if seg.end <= period.start:
            total += seg.duration
        elif seg.start >= period.end:
            if until is not None and seg.start >= until:
                continue
            end = seg.end if until is None else min(seg.end, until)
            total += max(0.0, (end - seg.start).total_seconds())
    return total


def _is_valid_tx_split(
    a: _SbPeriod,
    b: _SbPeriod,
    segments: Sequence[_Segment],
) -> bool:
    """PDF §5.3 (b): two SB periods ≥2h each, ≥8h total, 12h/15h constraints."""
    if a.duration < TX_SPLIT_MIN_PERIOD_SECONDS:
        return False
    if b.duration < TX_SPLIT_MIN_PERIOD_SECONDS:
        return False
    if a.duration + b.duration < TX_SPLIT_TOTAL_MIN_SECONDS:
        return False

    first, second = sorted((a, b), key=lambda p: p.start)
    # Driving before+after each period must not exceed 12h.
    if _driving_around_period(segments, first, until=second.start) > TX_MAX_DRIVING_SECONDS:
        return False
    if _driving_around_period(segments, second) > TX_MAX_DRIVING_SECONDS:
        return False
    # On-duty before+after each period: no driving after the 15th hour of that sum.
    # Equivalent practical check: accumulated duty around each period ≤ 15h when
    # the later segment still includes driving past that threshold is rejected by
    # requiring the around-period duty sum itself ≤ 15h at pairing time.
    if _duty_around_period(segments, first, until=second.start) > TX_MAX_DUTY_SECONDS:
        return False
    if _duty_around_period(segments, second) > TX_MAX_DUTY_SECONDS:
        return False
    return True


def run_tx_state_machine(timeline: DriverTimeline) -> TxStateMachineResult:
    """Process a driver timeline under Texas intrastate clocks."""
    result = TxStateMachineResult()
    if not timeline.events:
        return result

    events = build_timeline_from_logs(list(timeline.events))
    as_of = events[-1].timestamp

    consecutive_rest_start: Optional[datetime] = None
    consecutive_rest_seconds: float = 0.0
    consecutive_sb_seconds: float = 0.0
    sb_block_start: Optional[datetime] = None

    shift_segments: List[_Segment] = []
    pending_sb: List[_SbPeriod] = []
    rematch_anchor: Optional[datetime] = None

    def _try_close_sb_period(end_ts: datetime) -> None:
        nonlocal rematch_anchor, pending_sb, shift_segments

        if (
            result.current_shift is None
            or sb_block_start is None
            or consecutive_sb_seconds < TX_SPLIT_MIN_PERIOD_SECONDS
        ):
            return
        # Qualifying ≥8h rest is a full reset, not a split period.
        if consecutive_rest_seconds >= TX_QUALIFYING_OFF_DUTY_SECONDS:
            return

        period = _SbPeriod(
            start=sb_block_start,
            end=end_ts,
            duration=consecutive_sb_seconds,
        )
        paired_with: Optional[_SbPeriod] = None
        for prior in pending_sb:
            if prior.paired:
                continue
            if _is_valid_tx_split(prior, period, shift_segments):
                paired_with = prior
                break

        if paired_with is None:
            pending_sb.append(period)
            return

        paired_with.paired = True
        period.paired = True
        first, second = sorted((paired_with, period), key=lambda p: p.start)
        result.split_sleeper_active = True
        rematch_anchor = first.end
        result.duty_window_start = first.end
        result.split_excluded_intervals = [
            (first.start, first.end),
            (second.start, second.end),
        ]
        duty, driving = _recompute_from_segments(
            shift_segments, rematch_anchor=rematch_anchor
        )
        assert result.current_shift is not None
        result.current_shift.cumulative_duty_seconds = duty
        result.current_shift.cumulative_driving_seconds = driving
        result.accumulated_duty_seconds = duty
        result.total_driving_seconds = driving
        result.total_duty_seconds = duty

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
        is_sb = status == CanonicalDutyStatus.SLEEPER_BERTH

        if is_rest:
            if consecutive_rest_start is None:
                consecutive_rest_start = event.timestamp
            consecutive_rest_seconds += duration
            if is_sb:
                if sb_block_start is None:
                    sb_block_start = event.timestamp
                    consecutive_sb_seconds = 0.0
                consecutive_sb_seconds += duration
            else:
                # Leaving SB closes a potential split period.
                if consecutive_sb_seconds >= TX_SPLIT_MIN_PERIOD_SECONDS:
                    _try_close_sb_period(event.timestamp)
                sb_block_start = None
                consecutive_sb_seconds = 0.0
        else:
            if consecutive_sb_seconds >= TX_SPLIT_MIN_PERIOD_SECONDS:
                _try_close_sb_period(event.timestamp)

            if consecutive_rest_seconds >= TX_QUALIFYING_OFF_DUTY_SECONDS:
                if result.current_shift is not None:
                    result.shifts.append(result.current_shift)
                result.current_shift = ShiftWindow(
                    shift_start=event.timestamp,
                    qualifying_rest_before=consecutive_rest_start or event.timestamp,
                )
                result.duty_window_start = None
                result.duty_window_elapsed_seconds = 0.0
                result.accumulated_duty_seconds = 0.0
                result.split_sleeper_active = False
                result.split_excluded_intervals = []
                result.last_qualifying_rest_end = event.timestamp
                shift_segments = []
                pending_sb = []
                rematch_anchor = None

                # TX restart: ≥34h OFF/SB/PC resets weekly — no 1–5 AM gate.
                if consecutive_rest_seconds >= RESTART_SECONDS:
                    result.had_34h_restart = True
                    result.last_valid_restart_at = event.timestamp

            consecutive_rest_start = None
            consecutive_rest_seconds = 0.0
            sb_block_start = None
            consecutive_sb_seconds = 0.0

        if result.current_shift is not None and duration > 0:
            shift_segments.append(_Segment(start=seg_start, end=seg_end, status=status))

        if result.current_shift is not None:
            if is_duty and result.duty_window_start is None:
                result.duty_window_start = event.timestamp

            if rematch_anchor is None:
                if is_driving:
                    result.current_shift.cumulative_driving_seconds += duration
                    result.total_driving_seconds += duration
                if is_duty:
                    result.current_shift.cumulative_duty_seconds += duration
                    result.accumulated_duty_seconds += duration
                    result.total_duty_seconds = (
                        result.current_shift.cumulative_duty_seconds
                    )
            else:
                duty, driving = _recompute_from_segments(
                    shift_segments, rematch_anchor=rematch_anchor
                )
                result.current_shift.cumulative_duty_seconds = duty
                result.current_shift.cumulative_driving_seconds = driving
                result.accumulated_duty_seconds = duty
                result.total_driving_seconds = driving
                result.total_duty_seconds = duty

    if result.current_shift is not None:
        result.total_duty_seconds = result.current_shift.cumulative_duty_seconds
        result.accumulated_duty_seconds = result.current_shift.cumulative_duty_seconds
        result.total_driving_seconds = result.current_shift.cumulative_driving_seconds
        if result.duty_window_start is not None:
            result.duty_window_elapsed_seconds = max(
                0.0, (as_of - result.duty_window_start).total_seconds()
            )

    result.consecutive_rest_seconds = consecutive_rest_seconds
    return result
