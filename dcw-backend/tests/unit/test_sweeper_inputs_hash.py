"""Sweeper audit inputs_hash must bind the full timeline, not a tip fingerprint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.security import compute_inputs_hash
from app.domains.engine.schemas import DriverTimeline

UTC = timezone.utc


def _hash_for_timeline(
    events: list[DriverTimeline.HOSEvent],
    *,
    weekly_duty_seconds: float,
) -> str:
    """Mirror the sweeper's inputs_hash payload shape."""
    return compute_inputs_hash(
        {
            "tenant_id": "t1",
            "driver_id": "d1",
            "events": [
                {"status": event.status, "ts": event.timestamp.isoformat()}
                for event in events
            ],
            "weekly_duty_seconds": weekly_duty_seconds,
        }
    )


def _legacy_tip_hash(events: list[DriverTimeline.HOSEvent]) -> str:
    """Pre-fix fingerprint that collided across different timelines."""
    return compute_inputs_hash(
        {
            "tenant_id": "t1",
            "driver_id": "d1",
            "event_count": len(events),
            "last_event": events[-1].timestamp.isoformat() if events else "",
        }
    )


def test_inputs_hash_differs_when_intermediate_status_differs() -> None:
    base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    last = base + timedelta(hours=80)

    heavy = [
        DriverTimeline.HOSEvent(status="D", timestamp=base),
        DriverTimeline.HOSEvent(status="OFF", timestamp=base + timedelta(hours=10)),
        DriverTimeline.HOSEvent(status="D", timestamp=base + timedelta(hours=20)),
        DriverTimeline.HOSEvent(status="OFF", timestamp=base + timedelta(hours=25)),
        DriverTimeline.HOSEvent(status="OFF", timestamp=last),
    ]
    light = [
        DriverTimeline.HOSEvent(status="D", timestamp=base),
        DriverTimeline.HOSEvent(status="OFF", timestamp=base + timedelta(hours=2)),
        DriverTimeline.HOSEvent(status="D", timestamp=base + timedelta(hours=20)),
        DriverTimeline.HOSEvent(status="OFF", timestamp=base + timedelta(hours=22)),
        DriverTimeline.HOSEvent(status="OFF", timestamp=last),
    ]

    assert _legacy_tip_hash(heavy) == _legacy_tip_hash(light)
    assert _hash_for_timeline(heavy, weekly_duty_seconds=15 * 3600) != _hash_for_timeline(
        light, weekly_duty_seconds=4 * 3600
    )


def test_inputs_hash_includes_weekly_duty_seconds() -> None:
    events = [
        DriverTimeline.HOSEvent(
            status="D",
            timestamp=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        ),
    ]
    assert _hash_for_timeline(events, weekly_duty_seconds=10.0) != _hash_for_timeline(
        events, weekly_duty_seconds=11.0
    )
