"""Point-in-time replay helpers for historical compliance backtesting."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from app.domains.engine.schemas import DriverTimeline
from app.domains.ingestion.duty_filter import should_skip_duty_status_change
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
) -> list[DriverTimeline.HOSEvent]:
    """Convert canonical HOS logs to timeline events (chronological)."""
    sorted_logs = sorted(logs, key=lambda log: log.event_timestamp)
    return [
        DriverTimeline.HOSEvent(
            status=log.status.value,
            timestamp=log.event_timestamp,
        )
        for log in sorted_logs
        if not should_skip_duty_status_change(log.status, log.raw_payload)
    ]


def truncate_timeline_to(
    timeline: DriverTimeline,
    as_of: datetime,
) -> DriverTimeline:
    """Keep events up to ``as_of`` and close the open segment with a synthetic event."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    else:
        as_of = as_of.astimezone(UTC)

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
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def is_valid_restart_period(
    rest_start: datetime,
    rest_end: datetime,
    *,
    home_terminal_tz: object | None = None,
) -> bool:
    """Return True when rest is at least 34 consecutive hours.

    ``home_terminal_tz`` is accepted for call-site compatibility but ignored —
    the obsolete two 1–5 AM home-terminal periods gate was removed in
    ``fmcsa-us-property@2.5.0``.
    """
    del home_terminal_tz  # unused; kept for API compatibility
    rest_start = _normalize_utc(rest_start)
    rest_end = _normalize_utc(rest_end)
    return (rest_end - rest_start).total_seconds() >= RESTART_SECONDS


def _restart_reset_from_rest(
    rest_start: datetime,
    boundary: datetime,
    *,
    still_resting: bool,
) -> datetime | None:
    """Derive weekly-cycle reset point from a completed or in-progress rest block."""
    rest_start = _normalize_utc(rest_start)
    boundary = _normalize_utc(boundary)

    if still_resting:
        restart_complete_at = rest_start + timedelta(seconds=RESTART_SECONDS)
        if boundary < restart_complete_at:
            return None
        return restart_complete_at

    if not is_valid_restart_period(rest_start, boundary):
        return None
    return boundary


def find_restart_reset_point(
    events: Sequence[DriverTimeline.HOSEvent],
    as_of: datetime,
    *,
    home_terminal_tz: object | None = None,
) -> datetime | None:
    """Return the latest valid 34h restart reset point at or before ``as_of``."""
    del home_terminal_tz  # unused; kept for API compatibility
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
    home_terminal_tz: object | None = None,
) -> float:
    """Sum on-duty seconds in the rolling weekly window ending at ``as_of``.

    Credits a valid 34-hour OFF/SB restart: duty before the reset point is
    excluded from the rolling total.
    """
    del home_terminal_tz  # unused; kept for API compatibility
    as_of = _normalize_utc(as_of)

    cutoff = as_of - timedelta(days=cycle_days)
    reset_point = find_restart_reset_point(events, as_of)
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
