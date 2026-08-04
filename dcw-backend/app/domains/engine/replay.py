"""Point-in-time replay helpers for historical compliance backtesting."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import List, Sequence
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.domains.engine.schemas import DriverTimeline
from app.domains.ingestion.duty_filter import should_skip_duty_status_change
from app.domains.ingestion.hos_versions import select_latest_hos_versions
from app.domains.ingestion.schemas import CanonicalDutyStatus, DCWCanonicalHOSLog

_DUTY_STATUSES = {
    CanonicalDutyStatus.ON_DUTY.value,
    CanonicalDutyStatus.DRIVING.value,
    CanonicalDutyStatus.YARD_MOVE.value,
}

_REST_STATUSES = {
    CanonicalDutyStatus.OFF_DUTY.value,
    CanonicalDutyStatus.SLEEPER_BERTH.value,
    CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
}

RESTART_SECONDS: float = 34 * 3600.0

# Extra days before the rolling window so 34h rest starting just before cutoff is visible.
WEEKLY_DUTY_LOOKBACK_BUFFER_DAYS: int = 3


def logs_to_timeline_events(
    logs: Sequence[DCWCanonicalHOSLog],
) -> List[DriverTimeline.HOSEvent]:
    """Convert canonical HOS logs to timeline events (chronological)."""
    latest_logs = select_latest_hos_versions(logs)
    return [
        DriverTimeline.HOSEvent(
            status=log.status.value,
            timestamp=log.event_timestamp,
        )
        for log in latest_logs
        if not should_skip_duty_status_change(log.status, log.raw_payload)
    ]


def truncate_timeline_to(
    timeline: DriverTimeline,
    as_of: datetime,
) -> DriverTimeline:
    """Keep events up to ``as_of`` and close the open segment with a synthetic event."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of = as_of.astimezone(timezone.utc)

    events = [
        DriverTimeline.HOSEvent(status=e.status, timestamp=e.timestamp)
        for e in timeline.events
        if e.timestamp <= as_of
    ]
    if not events:
        return DriverTimeline(
            driver_id=timeline.driver_id,
            tenant_id=timeline.tenant_id,
            events=[],
        )

    events.sort(key=lambda e: e.timestamp)
    last = events[-1]
    if last.timestamp < as_of:
        events.append(
            DriverTimeline.HOSEvent(
                status=last.status,
                timestamp=as_of,
                duration_seconds=0.0,
            )
        )

    from app.domains.engine.state_machine import build_timeline_from_logs

    build_timeline_from_logs(events)
    return DriverTimeline(
        driver_id=timeline.driver_id,
        tenant_id=timeline.tenant_id,
        events=events,
    )


def _normalize_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def count_1_to_5_am_periods(
    start: datetime,
    end: datetime,
    home_terminal_tz: ZoneInfo,
) -> int:
    """Count calendar days where rest overlaps [01:00, 05:00) in home-terminal local time."""
    start = _normalize_utc(start)
    end = _normalize_utc(end)
    if start >= end:
        return 0

    start_local = start.astimezone(home_terminal_tz)
    end_local = end.astimezone(home_terminal_tz)
    day = start_local.date()
    last_day = end_local.date()
    count = 0

    while day <= last_day:
        period_start = datetime.combine(day, time(1, 0), tzinfo=home_terminal_tz)
        period_end = datetime.combine(day, time(5, 0), tzinfo=home_terminal_tz)
        overlap_start = max(start, period_start)
        overlap_end = min(end, period_end)
        if overlap_start < overlap_end:
            count += 1
        day += timedelta(days=1)

    return count


def is_valid_restart_period(
    rest_start: datetime,
    rest_end: datetime,
    *,
    home_terminal_tz: ZoneInfo,
) -> bool:
    """Return True when rest is at least 34h and spans two 1–5 AM home-terminal periods."""
    rest_start = _normalize_utc(rest_start)
    rest_end = _normalize_utc(rest_end)
    duration = (rest_end - rest_start).total_seconds()
    if duration < RESTART_SECONDS:
        return False
    return count_1_to_5_am_periods(rest_start, rest_end, home_terminal_tz) >= 2


def _restart_reset_from_rest(
    rest_start: datetime,
    boundary: datetime,
    *,
    home_terminal_tz: ZoneInfo,
    still_resting: bool,
) -> datetime | None:
    """Derive weekly-cycle reset point from a completed or in-progress rest block."""
    rest_start = _normalize_utc(rest_start)
    boundary = _normalize_utc(boundary)

    if still_resting:
        restart_complete_at = rest_start + timedelta(seconds=RESTART_SECONDS)
        if boundary < restart_complete_at:
            return None
        if not is_valid_restart_period(
            rest_start,
            restart_complete_at,
            home_terminal_tz=home_terminal_tz,
        ):
            return None
        return restart_complete_at

    if not is_valid_restart_period(
        rest_start,
        boundary,
        home_terminal_tz=home_terminal_tz,
    ):
        return None
    return boundary


def find_restart_reset_point(
    events: Sequence[DriverTimeline.HOSEvent],
    as_of: datetime,
    *,
    home_terminal_tz: ZoneInfo | None = None,
) -> datetime | None:
    """Return the latest valid 34h restart reset point at or before ``as_of``."""
    tz = home_terminal_tz or ZoneInfo(settings.DEFAULT_HOME_TERMINAL_TIMEZONE)
    as_of = _normalize_utc(as_of)

    sorted_events = sorted(
        [e for e in events if e.timestamp <= as_of],
        key=lambda e: e.timestamp,
    )
    if not sorted_events:
        return None

    latest_reset: datetime | None = None
    rest_start: datetime | None = None

    for i, event in enumerate(sorted_events):
        seg_end = sorted_events[i + 1].timestamp if i + 1 < len(sorted_events) else as_of
        is_rest = event.status in _REST_STATUSES

        if is_rest:
            if rest_start is None:
                rest_start = event.timestamp
            continue

        if rest_start is not None:
            reset = _restart_reset_from_rest(
                rest_start,
                event.timestamp,
                home_terminal_tz=tz,
                still_resting=False,
            )
            if reset is not None and reset <= as_of:
                latest_reset = reset
            rest_start = None

        _ = seg_end  # segment boundary used implicitly via next event timestamp

    if rest_start is not None:
        reset = _restart_reset_from_rest(
            rest_start,
            as_of,
            home_terminal_tz=tz,
            still_resting=True,
        )
        if reset is not None and reset <= as_of:
            latest_reset = reset

    return latest_reset


def compute_weekly_duty_seconds(
    events: Sequence[DriverTimeline.HOSEvent],
    as_of: datetime,
    cycle_days: int = 8,
    *,
    home_terminal_tz: ZoneInfo | None = None,
) -> float:
    """Sum on-duty seconds in the rolling weekly window ending at ``as_of``.

    Credits a valid 34-hour OFF/SB restart: duty before the reset point is
    excluded from the rolling total.
    """
    as_of = _normalize_utc(as_of)
    tz = home_terminal_tz or ZoneInfo(settings.DEFAULT_HOME_TERMINAL_TIMEZONE)

    cutoff = as_of - timedelta(days=cycle_days)
    reset_point = find_restart_reset_point(events, as_of, home_terminal_tz=tz)
    if reset_point is not None:
        cutoff = max(cutoff, reset_point)

    sorted_events = sorted(
        [e for e in events if e.timestamp <= as_of],
        key=lambda e: e.timestamp,
    )
    if not sorted_events:
        return 0.0

    total_seconds = 0.0
    for i, event in enumerate(sorted_events):
        if event.status not in _DUTY_STATUSES:
            continue
        seg_start = max(event.timestamp, cutoff)
        if seg_start >= as_of:
            continue
        if i + 1 < len(sorted_events):
            seg_end = min(sorted_events[i + 1].timestamp, as_of)
        else:
            seg_end = as_of
        if seg_end > seg_start:
            total_seconds += (seg_end - seg_start).total_seconds()

    return total_seconds
