"""Recompute compliance at an alert timestamp and build explanation + graph context."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.security import compute_inputs_hash
from app.domains.dashboard.day_builder import LANE_FOR_STATUS, format_duration_hhmm
from app.domains.dashboard.timezone import zoneinfo_for
from app.domains.engine.calculators import (
    MAX_DRIVING_BEFORE_BREAK_SECONDS,
    MAX_DRIVING_SECONDS,
    MAX_DUTY_WINDOW_SECONDS,
)
from app.domains.engine.replay import (
    compute_weekly_duty_seconds,
    find_restart_reset_point,
    truncate_timeline_to,
)
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline, Violation, ViolationType
from app.domains.engine.state_machine import run_state_machine
from app.domains.ingestion.duty_filter import should_skip_duty_status_change
from app.domains.ingestion.schemas import CanonicalDutyStatus

RESTART_SECONDS = 34 * 3600.0


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours(seconds: float) -> float:
    return round(seconds / 3600.0, 2)


def _local_label(dt: datetime, tz: ZoneInfo) -> str:
    return _ensure_utc(dt).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def _match_violation(
    violations: Sequence[Violation],
    violation_type: str,
    as_of: datetime,
) -> Optional[Violation]:
    typed = [v for v in violations if v.violation_type.value == violation_type]
    if not typed:
        return None
    as_of = _ensure_utc(as_of)
    return min(
        typed,
        key=lambda v: abs((_ensure_utc(v.detected_at) - as_of).total_seconds()),
    )


_DRIVING_STATUSES = frozenset(
    {
        CanonicalDutyStatus.DRIVING.value,
        CanonicalDutyStatus.YARD_MOVE.value,
    }
)
_DUTY_STATUSES = frozenset(
    {
        CanonicalDutyStatus.ON_DUTY.value,
        CanonicalDutyStatus.DRIVING.value,
        CanonicalDutyStatus.YARD_MOVE.value,
    }
)


def _segment_highlighted(
    status: str,
    seg_start: datetime,
    seg_end: datetime,
    *,
    violation_type: str,
    causal_start: Optional[datetime],
    as_of: datetime,
) -> bool:
    """Mark segments that contributed to the firing rule within the causal window."""
    if causal_start is None:
        return False
    # Overlap with [causal_start, as_of]
    if seg_end <= causal_start or seg_start >= as_of:
        return False
    if violation_type == ViolationType.DRIVING_LIMIT.value:
        return status in _DRIVING_STATUSES
    if violation_type == ViolationType.DUTY_WINDOW.value:
        return status in _DUTY_STATUSES
    if violation_type == ViolationType.WEEKLY_CYCLE.value:
        return status in _DUTY_STATUSES
    if violation_type == ViolationType.REST_BREAK.value:
        return status == CanonicalDutyStatus.DRIVING.value
    return False


def _build_shift_window(
    *,
    violation_type: str,
    state: Any,
    reset_point: Optional[datetime],
    as_of: datetime,
    tz: ZoneInfo,
) -> Dict[str, Any]:
    """Describe the clock window used for the firing rule."""
    shift = state.current_shift
    shift_start = shift.shift_start if shift else None
    as_of = _ensure_utc(as_of)

    if violation_type in (
        ViolationType.DRIVING_LIMIT.value,
        ViolationType.DUTY_WINDOW.value,
        ViolationType.REST_BREAK.value,
    ):
        start = shift_start
        label = "Current shift"
        note = "Since last qualifying ≥10h OFF/SB rest"
        if violation_type == ViolationType.DUTY_WINDOW.value:
            note = "14h duty window start (after qualifying rest)"
        elif violation_type == ViolationType.REST_BREAK.value:
            note = "Driving stretch since last ≥30-min break (shift context)"
    elif violation_type == ViolationType.WEEKLY_CYCLE.value:
        start = as_of - timedelta(days=settings.WEEKLY_CYCLE_DAYS)
        label = f"Rolling {settings.WEEKLY_CYCLE_DAYS}-day window"
        note = "ON/D/YM duty counted toward the weekly cycle"
        if reset_point and reset_point > start:
            start = reset_point
            label = "Weekly window (after 34h restart)"
            note = f"Reset by valid 34h restart at {_local_label(reset_point, tz)}"
    else:
        start = shift_start
        label = "Evaluation window"
        note = "Clock context at alert time"

    return {
        "label": label,
        "start_utc": start.isoformat() if start else None,
        "start_local": _local_label(start, tz) if start else "n/a",
        "end_utc": as_of.isoformat(),
        "end_local": _local_label(as_of, tz),
        "note": note,
    }


def _context_events(
    events: Sequence[DriverTimeline.HOSEvent],
    as_of: datetime,
    tz: ZoneInfo,
    *,
    before_hours: float = 6.0,
    after_hours: float = 2.0,
    violation_type: str = "",
    causal_start: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Build zoomed OFF/SB/D/ON segments around ``as_of`` for the context graph."""
    as_of = _ensure_utc(as_of)
    window_start = as_of - timedelta(hours=before_hours)
    window_end = as_of + timedelta(hours=after_hours)
    if causal_start is not None:
        causal_start = _ensure_utc(causal_start)

    sorted_events = sorted(events, key=lambda e: _ensure_utc(e.timestamp))
    # Carry status into window
    carry: Optional[str] = None
    in_window: List[DriverTimeline.HOSEvent] = []
    for event in sorted_events:
        ts = _ensure_utc(event.timestamp)
        if ts < window_start:
            carry = event.status
        elif ts <= window_end:
            in_window.append(event)

    timeline: List[tuple[datetime, str]] = []
    if carry is not None:
        timeline.append((window_start, carry))
    for event in in_window:
        ts = _ensure_utc(event.timestamp)
        if timeline and timeline[-1][0] == ts:
            timeline[-1] = (ts, event.status)
        else:
            timeline.append((ts, event.status))

    if not timeline:
        return []

    span = (window_end - window_start).total_seconds() or 1.0
    out: List[Dict[str, Any]] = []
    for idx, (ts, status) in enumerate(timeline):
        next_ts = timeline[idx + 1][0] if idx + 1 < len(timeline) else window_end
        duration = max(0.0, (next_ts - ts).total_seconds())
        if status not in LANE_FOR_STATUS:
            continue
        offset_h = (ts - window_start).total_seconds() / 3600.0
        out.append(
            {
                "status": status,
                "lane": LANE_FOR_STATUS[status],
                "event_timestamp": ts.isoformat(),
                "local_timestamp": _local_label(ts, tz),
                "hour_offset": offset_h,
                "duration_seconds": duration,
                "duration_hhmm": format_duration_hhmm(duration),
                "fraction_start": (ts - window_start).total_seconds() / span,
                "fraction_end": (next_ts - window_start).total_seconds() / span,
                "highlighted": _segment_highlighted(
                    status,
                    ts,
                    next_ts,
                    violation_type=violation_type,
                    causal_start=causal_start,
                    as_of=as_of,
                ),
            }
        )
    return out


def _build_explanation(
    *,
    violation_type: str,
    state: Any,
    weekly_duty_seconds: float,
    weekly_limit_hours: float,
    reset_point: Optional[datetime],
    as_of: datetime,
    tz: ZoneInfo,
    overage_seconds: float,
) -> List[Dict[str, str]]:
    steps: List[Dict[str, str]] = []
    shift = state.current_shift
    shift_start = shift.shift_start if shift else None

    if violation_type == ViolationType.DRIVING_LIMIT.value:
        used = state.total_driving_seconds if shift is None else shift.cumulative_driving_seconds
        # Prefer shift cumulative when available
        if shift is not None:
            used = shift.cumulative_driving_seconds
        remaining = MAX_DRIVING_SECONDS - used
        steps.extend(
            [
                {
                    "step": "Shift start",
                    "value": _local_label(shift_start, tz) if shift_start else "n/a",
                    "note": "New shift after qualifying ≥10h OFF/SB rest",
                },
                {
                    "step": "Driving accumulated",
                    "value": f"{_hours(used)}h",
                    "note": "Sum of D/YM within the current shift",
                },
                {
                    "step": "11-hour driving limit",
                    "value": "11.0h",
                    "note": "§ 395.3(a)(3)(i)",
                },
                {
                    "step": "Remaining / overage",
                    "value": (
                        f"{_hours(overage_seconds)}h over"
                        if overage_seconds > 0
                        else f"{_hours(max(0.0, remaining))}h remaining"
                    ),
                    "note": "Alert fires at limit or within the warning threshold",
                },
            ]
        )
    elif violation_type == ViolationType.DUTY_WINDOW.value:
        used = state.duty_window_elapsed_seconds
        remaining = MAX_DUTY_WINDOW_SECONDS - used
        steps.extend(
            [
                {
                    "step": "Shift start (14h clock)",
                    "value": _local_label(shift_start, tz) if shift_start else "n/a",
                    "note": "14h window starts when duty begins after qualifying rest",
                },
                {
                    "step": "Duty window elapsed",
                    "value": f"{_hours(used)}h",
                    "note": "ON/D/YM time since shift start",
                },
                {
                    "step": "14-hour limit",
                    "value": "14.0h",
                    "note": "§ 395.3(a)(2)",
                },
                {
                    "step": "Remaining / overage",
                    "value": (
                        f"{_hours(overage_seconds)}h over"
                        if overage_seconds > 0
                        else f"{_hours(max(0.0, remaining))}h remaining"
                    ),
                    "note": "Driving past the window is a violation",
                },
            ]
        )
    elif violation_type == ViolationType.REST_BREAK.value:
        since = state.driving_since_break_seconds
        steps.extend(
            [
                {
                    "step": "Driving since last 30-min break",
                    "value": f"{_hours(since)}h",
                    "note": "Break must be ≥30 consecutive minutes OFF/SB",
                },
                {
                    "step": "8-hour threshold",
                    "value": f"{_hours(MAX_DRIVING_BEFORE_BREAK_SECONDS)}h",
                    "note": "§ 395.3(a)(3)(ii)",
                },
                {
                    "step": "Break required",
                    "value": "yes" if state.driving_since_break_seconds >= MAX_DRIVING_BEFORE_BREAK_SECONDS else "approaching",
                    "note": "Must take 30 minutes before continuing to drive",
                },
            ]
        )
    elif violation_type == ViolationType.WEEKLY_CYCLE.value:
        window_start = as_of - timedelta(days=settings.WEEKLY_CYCLE_DAYS)
        if reset_point and reset_point > window_start:
            window_note = f"Reset by valid 34h restart at {_local_label(reset_point, tz)}"
            window_start = reset_point
        else:
            window_note = f"Rolling {settings.WEEKLY_CYCLE_DAYS}-day window"
        steps.extend(
            [
                {
                    "step": "Weekly window start",
                    "value": _local_label(window_start, tz),
                    "note": window_note,
                },
                {
                    "step": "On-duty in window",
                    "value": f"{_hours(weekly_duty_seconds)}h",
                    "note": "ON/D/YM seconds after restart cutoff",
                },
                {
                    "step": f"{weekly_limit_hours:.0f}-hour limit",
                    "value": f"{weekly_limit_hours:.0f}.0h",
                    "note": "§ 395.3(b)",
                },
                {
                    "step": "Remaining / overage",
                    "value": (
                        f"{_hours(overage_seconds)}h over"
                        if overage_seconds > 0
                        else f"{_hours(max(0.0, weekly_limit_hours * 3600 - weekly_duty_seconds))}h remaining"
                    ),
                    "note": "34h OFF/SB with two 1–5 AM periods resets this clock",
                },
            ]
        )
    elif violation_type == ViolationType.RESTART_INVALID.value:
        steps.extend(
            [
                {
                    "step": "Consecutive rest",
                    "value": f"{_hours(state.consecutive_rest_seconds)}h",
                    "note": "OFF/SB must reach 34h for restart credit",
                },
                {
                    "step": "Required rest",
                    "value": "34.0h",
                    "note": "§ 395.3(c) plus two home-terminal 1–5 AM periods",
                },
                {
                    "step": "Why invalid",
                    "value": "missing duration and/or 1–5 AM periods",
                    "note": "Rest ended without meeting both requirements",
                },
            ]
        )
    else:
        steps.append(
            {
                "step": "Evaluation",
                "value": violation_type or "unknown",
                "note": "Recomputed at alert time with the active rule pack",
            }
        )
    return steps


def build_alert_detail(
    *,
    driver_id: str,
    tenant_id: str,
    driver_name: Optional[str],
    events: Sequence[DriverTimeline.HOSEvent],
    as_of: datetime,
    violation_type: str,
    source: str,
    display_tz_name: str,
    description_hint: str = "",
    severity_hint: str = "",
    rule_ref_hint: str = "",
) -> Dict[str, Any]:
    """Recompute compliance at ``as_of`` and return drawer payload dict."""
    as_of = _ensure_utc(as_of)
    tz = zoneinfo_for(display_tz_name)
    timeline = DriverTimeline(
        driver_id=driver_id,
        tenant_id=tenant_id,
        events=list(events),
    )
    truncated = truncate_timeline_to(timeline, as_of)
    weekly = compute_weekly_duty_seconds(
        truncated.events,
        as_of=as_of,
        cycle_days=settings.WEEKLY_CYCLE_DAYS,
        home_terminal_tz=ZoneInfo(settings.DEFAULT_HOME_TERMINAL_TIMEZONE),
    )
    reset_point = find_restart_reset_point(
        truncated.events,
        as_of,
        home_terminal_tz=ZoneInfo(settings.DEFAULT_HOME_TERMINAL_TIMEZONE),
    )
    inputs_hash = compute_inputs_hash(
        {
            "tenant_id": tenant_id,
            "driver_id": driver_id,
            "as_of": as_of.isoformat(),
            "event_count": len(truncated.events),
            "purpose": "alert_detail",
        }
    )
    pack = RulePack(version=settings.DEFAULT_RULE_PACK_VERSION)
    result = pack.evaluate(
        truncated,
        inputs_hash=inputs_hash,
        weekly_duty_seconds=weekly,
        as_of=as_of,
    )
    state = run_state_machine(truncated)

    matched = _match_violation(result.violations, violation_type, as_of)
    severity = matched.severity.value if matched else (severity_hint or "INFO")
    rule_ref = matched.rule_ref if matched else rule_ref_hint
    description = matched.description if matched else (
        description_hint
        or "No matching violation at recompute time (clocks shown at as_of)."
    )
    overage = matched.overage_seconds if matched else 0.0

    driving_used = 0.0
    if state.current_shift is not None:
        driving_used = state.current_shift.cumulative_driving_seconds
    duty_used = state.duty_window_elapsed_seconds
    weekly_limit = settings.WEEKLY_CYCLE_LIMIT_HOURS

    explanation = _build_explanation(
        violation_type=violation_type,
        state=state,
        weekly_duty_seconds=weekly,
        weekly_limit_hours=weekly_limit,
        reset_point=reset_point,
        as_of=as_of,
        tz=tz,
        overage_seconds=overage,
    )

    local_day = as_of.astimezone(tz).date()
    shift_window = _build_shift_window(
        violation_type=violation_type,
        state=state,
        reset_point=reset_point,
        as_of=as_of,
        tz=tz,
    )
    causal_start: Optional[datetime] = None
    if shift_window.get("start_utc"):
        causal_start = _ensure_utc(datetime.fromisoformat(shift_window["start_utc"]))
    context = _context_events(
        truncated.events,
        as_of,
        tz,
        violation_type=violation_type,
        causal_start=causal_start,
    )

    return {
        "meta": {
            "driver_id": driver_id,
            "driver_name": driver_name,
            "as_of": as_of,
            "local_time": _local_label(as_of, tz),
            "display_timezone": str(tz),
            "violation_type": violation_type,
            "severity": severity,
            "rule_ref": rule_ref or "",
            "description": description,
            "source": source,
            "rule_pack_version": result.rule_pack_version,
            "matched_on_recompute": matched is not None,
        },
        "clocks": {
            "driving_used_h": _hours(driving_used),
            "driving_remaining_h": _hours(max(0.0, result.driving_remaining_seconds)),
            "driving_limit_h": _hours(MAX_DRIVING_SECONDS),
            "duty_used_h": _hours(duty_used),
            "duty_remaining_h": _hours(max(0.0, result.duty_window_remaining_seconds)),
            "duty_limit_h": _hours(MAX_DUTY_WINDOW_SECONDS),
            "weekly_used_h": _hours(weekly),
            "weekly_remaining_h": round(result.weekly_hours_remaining, 2),
            "weekly_limit_h": weekly_limit,
            "break_required": result.break_required,
            "driving_since_break_h": _hours(state.driving_since_break_seconds),
            "consecutive_rest_h": _hours(state.consecutive_rest_seconds),
            "last_valid_restart_at": (
                state.last_valid_restart_at.isoformat()
                if getattr(state, "last_valid_restart_at", None)
                else (reset_point.isoformat() if reset_point else None)
            ),
            "had_34h_restart": bool(getattr(state, "had_34h_restart", False) or reset_point),
        },
        "explanation": explanation,
        "overage_seconds": overage,
        "context_events": context,
        "context_window": {
            "start_local": _local_label(as_of - timedelta(hours=6), tz),
            "end_local": _local_label(as_of + timedelta(hours=2), tz),
            "as_of_fraction": 6.0 / 8.0,
        },
        "shift_window": shift_window,
        "day_date": local_day.isoformat(),
    }


def logs_to_events(records: Sequence[Any]) -> List[DriverTimeline.HOSEvent]:
    """Convert ORM/canonical log-like objects to timeline events."""
    events: List[DriverTimeline.HOSEvent] = []
    for rec in records:
        status = getattr(rec, "status", None)
        ts = getattr(rec, "event_timestamp", None)
        if status is None or ts is None:
            continue
        status_val = status.value if hasattr(status, "value") else str(status)
        raw_payload = getattr(rec, "raw_payload", None)
        if should_skip_duty_status_change(status_val, raw_payload):
            continue
        events.append(
            DriverTimeline.HOSEvent(
                status=status_val,
                timestamp=_ensure_utc(ts),
            )
        )
    events.sort(key=lambda e: e.timestamp)
    return events
