"""Recompute compliance at an alert timestamp and build explanation + graph context."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
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
from app.domains.engine.schemas import DriverProfile, DriverTimeline, Violation, ViolationType
from app.domains.engine.state_machine import run_state_machine
from app.domains.ingestion.duty_filter import should_skip_duty_status_change
from app.domains.ingestion.schemas import CanonicalDutyStatus

RESTART_SECONDS = 34 * 3600.0


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _hours(seconds: float) -> float:
    return round(seconds / 3600.0, 2)


def _local_label(dt: datetime, tz: ZoneInfo) -> str:
    return _ensure_utc(dt).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def _match_violation(
    violations: Sequence[Violation],
    violation_type: str,
    as_of: datetime,
) -> Violation | None:
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
    causal_start: datetime | None,
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


def _restart_applies_in_window(
    reset_point: datetime | None,
    as_of: datetime,
) -> bool:
    """True when a valid 34h restart falls inside the rolling weekly window."""
    if reset_point is None:
        return False
    window_start = _ensure_utc(as_of) - timedelta(days=settings.WEEKLY_CYCLE_DAYS)
    return _ensure_utc(reset_point) > window_start


def _build_weekly_restart_section(
    *,
    reset_point: datetime | None,
    as_of: datetime,
    tz: ZoneInfo,
) -> dict[str, Any]:
    """Dedicated weekly / 34h restart context for every alert type."""
    as_of = _ensure_utc(as_of)
    cycle_days = settings.WEEKLY_CYCLE_DAYS
    window_start = as_of - timedelta(days=cycle_days)
    applies = _restart_applies_in_window(reset_point, as_of)

    if applies and reset_point is not None:
        restart_local = _local_label(reset_point, tz)
        return {
            "had_restart": True,
            "restart_at_utc": reset_point.isoformat(),
            "restart_at_local": restart_local,
            "window_mode": "after_34h_restart",
            "window_mode_label": "after 34h restart",
            "weekly_window_start_local": restart_local,
            "message": (
                f"Valid 34h restart at {restart_local}. "
                "Weekly duty is counted from that reset (≥34h consecutive OFF/SB)."
            ),
        }
    return {
        "had_restart": False,
        "restart_at_utc": None,
        "restart_at_local": None,
        "window_mode": "rolling_window",
        "window_mode_label": "rolling window",
        "weekly_window_start_local": _local_label(window_start, tz),
        "message": (
            f"No valid 34h restart in the rolling window — weekly clock is "
            f"unbroken rolling {cycle_days}-day."
        ),
    }


def _build_shift_window(
    *,
    violation_type: str,
    state: Any,
    reset_point: datetime | None,
    as_of: datetime,
    tz: ZoneInfo,
) -> dict[str, Any]:
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
    causal_start: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build zoomed OFF/SB/D/ON segments around ``as_of`` for the context graph."""
    as_of = _ensure_utc(as_of)
    window_start = as_of - timedelta(hours=before_hours)
    window_end = as_of + timedelta(hours=after_hours)
    if causal_start is not None:
        causal_start = _ensure_utc(causal_start)

    sorted_events = sorted(events, key=lambda e: _ensure_utc(e.timestamp))
    # Carry status into window
    carry: str | None = None
    in_window: list[DriverTimeline.HOSEvent] = []
    for event in sorted_events:
        ts = _ensure_utc(event.timestamp)
        if ts < window_start:
            carry = event.status
        elif ts <= window_end:
            in_window.append(event)

    timeline: list[tuple[datetime, str]] = []
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
    out: list[dict[str, Any]] = []
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


def _counts_as(status: str) -> str:
    """Classify a duty status for contributing-log display."""
    if status == CanonicalDutyStatus.DRIVING.value:
        return "driving"
    if status == CanonicalDutyStatus.ON_DUTY.value:
        return "duty"
    if status == CanonicalDutyStatus.YARD_MOVE.value:
        return "ym"
    if status == CanonicalDutyStatus.PERSONAL_CONVEYANCE.value:
        return "pc"
    if status in (
        CanonicalDutyStatus.OFF_DUTY.value,
        CanonicalDutyStatus.SLEEPER_BERTH.value,
    ):
        return "rest"
    return "other"


def _empty_contributing_totals() -> dict[str, Any]:
    zero = format_duration_hhmm(0.0)
    return {
        "D_seconds": 0.0,
        "ON_seconds": 0.0,
        "PC_seconds": 0.0,
        "YM_seconds": 0.0,
        "OFF_seconds": 0.0,
        "SB_seconds": 0.0,
        "D": zero,
        "ON": zero,
        "PC": zero,
        "YM": zero,
        "OFF": zero,
        "SB": zero,
        "rest_seconds": 0.0,
        "rest": zero,
        "contributed_seconds": 0.0,
        "contributed": zero,
    }


def _status_timeline_in_window(
    events: Sequence[DriverTimeline.HOSEvent],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, str]]:
    """Build contiguous (timestamp, status) points covering ``[window_start, window_end]``."""
    window_start = _ensure_utc(window_start)
    window_end = _ensure_utc(window_end)
    sorted_events = sorted(events, key=lambda e: _ensure_utc(e.timestamp))
    carry: str | None = None
    in_window: list[DriverTimeline.HOSEvent] = []
    for event in sorted_events:
        ts = _ensure_utc(event.timestamp)
        if ts < window_start:
            carry = event.status
        elif ts <= window_end:
            in_window.append(event)

    timeline: list[tuple[datetime, str]] = []
    if carry is not None:
        timeline.append((window_start, carry))
    for event in in_window:
        ts = _ensure_utc(event.timestamp)
        if timeline and timeline[-1][0] == ts:
            timeline[-1] = (ts, event.status)
        else:
            timeline.append((ts, event.status))
    return timeline


def _contributing_logs(
    events: Sequence[DriverTimeline.HOSEvent],
    *,
    causal_start: datetime | None,
    as_of: datetime,
    violation_type: str,
    tz: ZoneInfo,
    records_meta: dict[tuple[datetime, str], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build tabular duty-status segments for the causal clock window.

    Returns ``(entries, totals)``. Reconstructs at detail time from the truncated
    timeline; does not persist contributing log IDs.
    """
    totals = _empty_contributing_totals()
    if causal_start is None:
        return [], totals

    as_of = _ensure_utc(as_of)
    causal_start = _ensure_utc(causal_start)
    if as_of <= causal_start:
        return [], totals

    timeline = _status_timeline_in_window(events, causal_start, as_of)
    if not timeline:
        return [], totals

    meta = records_meta or {}
    seconds_by_status = {
        CanonicalDutyStatus.DRIVING.value: 0.0,
        CanonicalDutyStatus.ON_DUTY.value: 0.0,
        CanonicalDutyStatus.PERSONAL_CONVEYANCE.value: 0.0,
        CanonicalDutyStatus.YARD_MOVE.value: 0.0,
        CanonicalDutyStatus.OFF_DUTY.value: 0.0,
        CanonicalDutyStatus.SLEEPER_BERTH.value: 0.0,
    }
    contributed_seconds = 0.0
    out: list[dict[str, Any]] = []

    for idx, (ts, status) in enumerate(timeline):
        next_ts = timeline[idx + 1][0] if idx + 1 < len(timeline) else as_of
        if next_ts > as_of:
            next_ts = as_of
        if next_ts <= ts:
            continue
        duration = (next_ts - ts).total_seconds()
        contributed = _segment_highlighted(
            status,
            ts,
            next_ts,
            violation_type=violation_type,
            causal_start=causal_start,
            as_of=as_of,
        )
        if status in seconds_by_status:
            seconds_by_status[status] += duration
        if contributed:
            contributed_seconds += duration

        row_meta = meta.get((_ensure_utc(ts), status), {})
        # Carry-in rows are clipped to causal_start; resolve the event that set status.
        if not row_meta and ts == causal_start:
            latest: tuple[datetime, dict[str, Any]] | None = None
            for key, value in meta.items():
                key_ts, key_status = key
                key_ts = _ensure_utc(key_ts)
                if key_status == status and key_ts <= causal_start:
                    if latest is None or key_ts > latest[0]:
                        latest = (key_ts, value)
            if latest is not None:
                row_meta = latest[1]

        out.append(
            {
                "status": status,
                "lane": LANE_FOR_STATUS.get(status, status),
                "start_local": _local_label(ts, tz),
                "end_local": _local_label(next_ts, tz),
                "start_utc": ts.isoformat(),
                "end_utc": next_ts.isoformat(),
                "duration_hhmm": format_duration_hhmm(duration),
                "duration_seconds": duration,
                "contributed": contributed,
                "counts_as": _counts_as(status),
                "raw_id": row_meta.get("raw_id"),
                "latitude": row_meta.get("latitude"),
                "longitude": row_meta.get("longitude"),
            }
        )

    totals = {
        "D_seconds": seconds_by_status[CanonicalDutyStatus.DRIVING.value],
        "ON_seconds": seconds_by_status[CanonicalDutyStatus.ON_DUTY.value],
        "PC_seconds": seconds_by_status[CanonicalDutyStatus.PERSONAL_CONVEYANCE.value],
        "YM_seconds": seconds_by_status[CanonicalDutyStatus.YARD_MOVE.value],
        "OFF_seconds": seconds_by_status[CanonicalDutyStatus.OFF_DUTY.value],
        "SB_seconds": seconds_by_status[CanonicalDutyStatus.SLEEPER_BERTH.value],
        "D": format_duration_hhmm(seconds_by_status[CanonicalDutyStatus.DRIVING.value]),
        "ON": format_duration_hhmm(seconds_by_status[CanonicalDutyStatus.ON_DUTY.value]),
        "PC": format_duration_hhmm(
            seconds_by_status[CanonicalDutyStatus.PERSONAL_CONVEYANCE.value]
        ),
        "YM": format_duration_hhmm(seconds_by_status[CanonicalDutyStatus.YARD_MOVE.value]),
        "OFF": format_duration_hhmm(seconds_by_status[CanonicalDutyStatus.OFF_DUTY.value]),
        "SB": format_duration_hhmm(seconds_by_status[CanonicalDutyStatus.SLEEPER_BERTH.value]),
        "rest_seconds": (
            seconds_by_status[CanonicalDutyStatus.OFF_DUTY.value]
            + seconds_by_status[CanonicalDutyStatus.SLEEPER_BERTH.value]
        ),
        "rest": format_duration_hhmm(
            seconds_by_status[CanonicalDutyStatus.OFF_DUTY.value]
            + seconds_by_status[CanonicalDutyStatus.SLEEPER_BERTH.value]
        ),
        "contributed_seconds": contributed_seconds,
        "contributed": format_duration_hhmm(contributed_seconds),
    }
    return out, totals


def _build_explanation(
    *,
    violation_type: str,
    state: Any,
    weekly_duty_seconds: float,
    weekly_limit_hours: float,
    reset_point: datetime | None,
    as_of: datetime,
    tz: ZoneInfo,
    overage_seconds: float,
) -> list[dict[str, str]]:
    steps: list[dict[str, str]] = []
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
                    "note": "Sum of Driving within the current shift",
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
                {
                    "step": "Weekly cycle (context)",
                    "value": "See Weekly / 34h restart",
                    "note": (
                        "The weekly gauge reflects on-duty hours since a valid 34h restart "
                        "or an unbroken rolling window — not part of this 11h shift alert."
                    ),
                },
            ]
        )
    elif violation_type == ViolationType.DUTY_WINDOW.value:
        used = state.duty_window_elapsed_seconds
        remaining = MAX_DUTY_WINDOW_SECONDS - used
        clock_start = getattr(state, "duty_window_start", None) or shift_start
        steps.extend(
            [
                {
                    "step": "Shift start (14h clock)",
                    "value": _local_label(clock_start, tz) if clock_start else "n/a",
                    "note": "14h wall-clock starts at first ON/D/YM after qualifying rest",
                },
                {
                    "step": "Duty window elapsed",
                    "value": f"{_hours(used)}h",
                    "note": "Wall-clock since first ON/D/YM (split-sleeper periods excluded)",
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
                    "note": "Break must be ≥30 consecutive minutes non-driving (OFF/SB/ON/PC)",
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
                    "note": "≥34h consecutive OFF/SB resets this clock",
                },
            ]
        )
    elif violation_type == ViolationType.RESTART_INVALID.value:
        steps.extend(
            [
                {
                    "step": "Legacy finding",
                    "value": "RESTART_INVALID",
                    "note": "No longer emitted as of fmcsa-us-property@2.5.0",
                },
                {
                    "step": "Current rule",
                    "value": "≥34.0h consecutive OFF/SB",
                    "note": "§ 395.3(c) cycle reset — no 1–5 AM gate",
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
    driver_name: str | None,
    events: Sequence[DriverTimeline.HOSEvent],
    as_of: datetime,
    violation_type: str,
    source: str,
    display_tz_name: str,
    description_hint: str = "",
    severity_hint: str = "",
    rule_ref_hint: str = "",
    profile: DriverProfile | None = None,
    records_meta: dict[tuple[datetime, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        profile=profile,
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
    weekly_restart = _build_weekly_restart_section(
        reset_point=reset_point,
        as_of=as_of,
        tz=tz,
    )
    restart_applies = weekly_restart["had_restart"]

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
    causal_start: datetime | None = None
    if shift_window.get("start_utc"):
        causal_start = _ensure_utc(datetime.fromisoformat(shift_window["start_utc"]))
    context = _context_events(
        truncated.events,
        as_of,
        tz,
        violation_type=violation_type,
        causal_start=causal_start,
    )
    contrib_logs, contrib_totals = _contributing_logs(
        truncated.events,
        causal_start=causal_start,
        as_of=as_of,
        violation_type=violation_type,
        tz=tz,
        records_meta=records_meta,
    )
    contributing_window = ""
    if causal_start is not None:
        contributing_window = (
            f"{shift_window.get('label', 'Causal window')} · "
            f"{shift_window.get('start_local', '')} → {shift_window.get('end_local', '')}"
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
                weekly_restart["restart_at_utc"]
                if restart_applies
                else None
            ),
            "last_valid_restart_at_local": weekly_restart.get("restart_at_local"),
            "had_34h_restart": restart_applies,
            "weekly_window_mode": weekly_restart["window_mode"],
            "weekly_window_subtitle": weekly_restart["window_mode_label"],
        },
        "weekly_restart": weekly_restart,
        "explanation": explanation,
        "overage_seconds": overage,
        "context_events": context,
        "context_window": {
            "start_local": _local_label(as_of - timedelta(hours=6), tz),
            "end_local": _local_label(as_of + timedelta(hours=2), tz),
            "as_of_fraction": 6.0 / 8.0,
        },
        "shift_window": shift_window,
        "contributing_logs": contrib_logs,
        "contributing_log_totals": contrib_totals,
        "contributing_window": contributing_window,
        "day_date": local_day.isoformat(),
    }


def logs_meta_map(records: Sequence[Any]) -> dict[tuple[datetime, str], dict[str, Any]]:
    """Build ``(timestamp, status) → {raw_id, lat, lon}`` from ORM log rows."""
    meta: dict[tuple[datetime, str], dict[str, Any]] = {}
    for rec in records:
        status = getattr(rec, "status", None)
        ts = getattr(rec, "event_timestamp", None)
        if status is None or ts is None:
            continue
        status_val = status.value if hasattr(status, "value") else str(status)
        raw_payload = getattr(rec, "raw_payload", None)
        if should_skip_duty_status_change(status_val, raw_payload):
            continue
        meta[(_ensure_utc(ts), status_val)] = {
            "raw_id": getattr(rec, "raw_id", None),
            "latitude": getattr(rec, "latitude", None),
            "longitude": getattr(rec, "longitude", None),
        }
    return meta


def logs_to_events(records: Sequence[Any]) -> list[DriverTimeline.HOSEvent]:
    """Convert ORM/canonical log-like objects to timeline events."""
    events: list[DriverTimeline.HOSEvent] = []
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
