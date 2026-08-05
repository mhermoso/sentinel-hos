"""Unit tests for historical replay and alert-lock simulation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domains.engine.replay import compute_weekly_duty_seconds, truncate_timeline_to
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline
from app.domains.ingestion.schemas import CanonicalDutyStatus
from app.domains.notifier.backtest_lock import InMemoryAlertLock

UTC = UTC


def _ts(hours: float) -> datetime:
    base = datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC)
    return base + timedelta(hours=hours)


def _timeline_with_driving_shift() -> DriverTimeline:
    """10h off, then 10h driving — should warn/violate 11h limit depending on as_of."""
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(10)),
    ]
    return DriverTimeline(driver_id="drv1", tenant_id="tenant1", events=events)


def test_truncate_timeline_adds_synthetic_close_event() -> None:
    timeline = _timeline_with_driving_shift()
    as_of = _ts(20)  # 10h into driving

    truncated = truncate_timeline_to(timeline, as_of)
    assert len(truncated.events) == 3
    assert truncated.events[-1].timestamp == as_of
    assert truncated.events[-1].status == CanonicalDutyStatus.DRIVING.value
    assert truncated.events[-2].duration_seconds == pytest.approx(10 * 3600.0)


def test_as_of_changes_violation_output() -> None:
    pack = RulePack()
    timeline = _timeline_with_driving_shift()

    early = pack.evaluate(
        timeline,
        inputs_hash="hash-early",
        weekly_duty_seconds=0.0,
        as_of=_ts(15),  # 5h driving
    )
    late = pack.evaluate(
        timeline,
        inputs_hash="hash-late",
        weekly_duty_seconds=0.0,
        as_of=_ts(21),  # 11h driving
    )

    assert len(early.violations) <= len(late.violations)
    assert any(v.severity.value == "VIOLATION" for v in late.violations)


def test_in_memory_lock_dedupes_repeated_warning() -> None:
    lock = InMemoryAlertLock()
    tenant, driver, shift = "t1", "d1", "20260301"
    rule, stage = "DRIVING_LIMIT", "WARNING"

    assert lock.would_dispatch(tenant, driver, shift, rule, stage) is True
    assert lock.would_dispatch(tenant, driver, shift, rule, stage) is False
    assert lock.would_suppress(tenant, driver, shift, rule, stage) is True


def test_compute_weekly_duty_extends_last_segment_to_as_of() -> None:
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(0)),
    ]
    as_of = _ts(2)
    seconds = compute_weekly_duty_seconds(events, as_of=as_of, cycle_days=8)
    assert seconds == pytest.approx(2 * 3600.0)
