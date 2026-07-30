"""Point-in-time replay helpers for historical compliance backtesting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Sequence

from app.domains.engine.schemas import DriverTimeline
from app.domains.engine.state_machine import build_timeline_from_logs
from app.domains.ingestion.schemas import CanonicalDutyStatus, DCWCanonicalHOSLog

_DUTY_STATUSES = {
    CanonicalDutyStatus.ON_DUTY.value,
    CanonicalDutyStatus.DRIVING.value,
    CanonicalDutyStatus.YARD_MOVE.value,
}


def logs_to_timeline_events(
    logs: Sequence[DCWCanonicalHOSLog],
) -> List[DriverTimeline.HOSEvent]:
    """Convert canonical HOS logs to timeline events (chronological)."""
    sorted_logs = sorted(logs, key=lambda log: log.event_timestamp)
    return [
        DriverTimeline.HOSEvent(
            status=log.status.value,
            timestamp=log.event_timestamp,
        )
        for log in sorted_logs
        if log.status != CanonicalDutyStatus.UNKNOWN
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

    build_timeline_from_logs(events)
    return DriverTimeline(
        driver_id=timeline.driver_id,
        tenant_id=timeline.tenant_id,
        events=events,
    )


def compute_weekly_duty_seconds(
    events: Sequence[DriverTimeline.HOSEvent],
    as_of: datetime,
    cycle_days: int = 8,
) -> float:
    """Sum on-duty seconds in the rolling weekly window ending at ``as_of``.

    Uses the full status timeline so OFF/SB events close duty segments.
    Passing only duty-status rows would treat gaps between duty starts as
    on-duty time and badly over-count the 60/70h cycle.
    """
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of = as_of.astimezone(timezone.utc)

    cutoff = as_of - timedelta(days=cycle_days)
    window_events = sorted(
        [
            e
            for e in events
            if e.timestamp >= cutoff
            and e.timestamp <= as_of
            and e.status != CanonicalDutyStatus.UNKNOWN.value
        ],
        key=lambda e: e.timestamp,
    )

    if not window_events:
        return 0.0

    total_seconds = 0.0
    for i, event in enumerate(window_events):
        if event.status not in _DUTY_STATUSES:
            continue
        end = window_events[i + 1].timestamp if i + 1 < len(window_events) else as_of
        total_seconds += max(0.0, (end - event.timestamp).total_seconds())

    return total_seconds
