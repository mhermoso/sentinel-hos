"""Unit tests for 34-hour restart weekly cycle reset (no 1–5 AM gate)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.security import compute_inputs_hash
from app.domains.engine.calculators import check_restart
from app.domains.engine.replay import (
    compute_weekly_duty_seconds,
    find_restart_reset_point,
    is_valid_restart_period,
    logs_to_timeline_events,
)
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline, ViolationType
from app.domains.engine.state_machine import StateMachineResult, run_state_machine
from app.domains.ingestion.schemas import CanonicalDutyStatus, DCWCanonicalHOSLog

UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")
_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "hos_30d_canonical.json"
if not _DATA_PATH.exists():
    _DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "hos_10d_canonical.json"


def _ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _heavy_duty_then_restart_timeline() -> list[DriverTimeline.HOSEvent]:
    """70h duty, then 35h OFF, then ON."""
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(2026, 7, 10, 0)),
    ]
    duty_start = _ts(2026, 7, 10, 10)
    events.append(DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=duty_start))
    events.append(
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=duty_start + timedelta(hours=70),
        )
    )
    restart_start = duty_start + timedelta(hours=70)
    events.append(
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.ON_DUTY.value,
            timestamp=restart_start + timedelta(hours=35),
        )
    )
    return events


def test_is_valid_restart_requires_34h_only() -> None:
    start = _ts(2026, 7, 21, 11)
    assert is_valid_restart_period(start, start + timedelta(hours=33, minutes=59)) is False
    assert is_valid_restart_period(start, start + timedelta(hours=34)) is True
    # home_terminal_tz ignored
    assert is_valid_restart_period(
        start,
        start + timedelta(hours=34),
        home_terminal_tz=CHICAGO,
    )


def test_34h_restart_resets_weekly_duty() -> None:
    events = _heavy_duty_then_restart_timeline()
    as_of = events[-1].timestamp + timedelta(hours=1)
    weekly = compute_weekly_duty_seconds(
        events,
        as_of=as_of,
        cycle_days=8,
        home_terminal_tz=CHICAGO,
    )
    assert weekly < 70 * 3600


def test_34h_restart_without_two_am_periods_still_resets() -> None:
    """≥34h OFF resets weekly duty even when only one 1–5 AM local day overlaps."""
    off_start = _ts(2026, 7, 21, 11)
    on_duty = off_start + timedelta(hours=34)
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(2026, 7, 20, 0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(2026, 7, 20, 10)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=off_start),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.ON_DUTY.value, timestamp=on_duty),
    ]
    as_of = on_duty
    reset = find_restart_reset_point(events, as_of, home_terminal_tz=CHICAGO)
    assert reset == on_duty
    weekly = compute_weekly_duty_seconds(events, as_of=as_of, cycle_days=8, home_terminal_tz=CHICAGO)
    assert weekly == pytest.approx(0.0)


def test_check_restart_never_emits() -> None:
    state = StateMachineResult(had_34h_restart=True)
    assert check_restart(state, _ts(2026, 7, 22, 12)) == []


def test_state_machine_credits_34h_restart() -> None:
    off_start = _ts(2026, 7, 20, 0)
    on_duty = off_start + timedelta(hours=35)
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=off_start),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.ON_DUTY.value, timestamp=on_duty),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.ON_DUTY.value,
            timestamp=on_duty + timedelta(hours=1),
        ),
    ]
    timeline = DriverTimeline(driver_id="d1", tenant_id="t1", events=events)
    result = run_state_machine(timeline)
    assert result.had_34h_restart is True
    assert result.last_valid_restart_at == on_duty


def test_rolling_window_ages_out_without_34h_rest() -> None:
    """Without a 34h rest, only duty inside the trailing 8-day window counts."""
    events: list[DriverTimeline.HOSEvent] = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(2026, 1, 1, 0)),
    ]
    # 10 calendar days of 4h driving with ~20h OFF between — never ≥34h consecutive rest
    for day in range(10):
        drive_start = _ts(2026, 1, 1 + day, 8)
        drive_end = drive_start + timedelta(hours=4)
        events.append(
            DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=drive_start)
        )
        events.append(
            DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=drive_end)
        )
    as_of = _ts(2026, 1, 11, 8)
    weekly = compute_weekly_duty_seconds(events, as_of=as_of, cycle_days=8, home_terminal_tz=CHICAGO)
    # Cutoff = Jan 3 08:00 → full 4h days on Jan 3..Jan 10 = 8 days
    assert weekly == pytest.approx(8 * 4 * 3600.0)


@pytest.mark.skipif(not _DATA_PATH.exists(), reason="hos_10d_canonical.json not present")
def test_cesar_garza_scenario() -> None:
    """Cesar Garza b382: 45h OFF 7/25–7/26 should reset weekly clock."""
    grouped = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    logs = [DCWCanonicalHOSLog.model_validate(r) for r in grouped["b382"]]
    events = logs_to_timeline_events(logs)
    as_of = datetime(2026, 7, 26, 22, 11, 4, tzinfo=UTC)

    weekly = compute_weekly_duty_seconds(
        events,
        as_of=as_of,
        cycle_days=settings.WEEKLY_CYCLE_DAYS,
        home_terminal_tz=CHICAGO,
    )
    assert weekly < settings.WEEKLY_CYCLE_LIMIT_HOURS * 3600

    timeline = DriverTimeline(driver_id="b382", tenant_id="tenant", events=events)
    pack = RulePack(version=settings.DEFAULT_RULE_PACK_VERSION)
    result = pack.evaluate(
        timeline,
        inputs_hash=compute_inputs_hash({"driver_id": "b382", "as_of": as_of.isoformat()}),
        weekly_duty_seconds=weekly,
        as_of=as_of,
    )
    weekly_violations = [v for v in result.violations if v.violation_type == ViolationType.WEEKLY_CYCLE]
    assert not weekly_violations
    assert not any(v.violation_type == ViolationType.RESTART_INVALID for v in result.violations)

    reset = find_restart_reset_point(events, as_of, home_terminal_tz=CHICAGO)
    assert reset is not None
    assert reset <= as_of
