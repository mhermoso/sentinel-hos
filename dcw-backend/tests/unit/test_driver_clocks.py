"""Unit tests for driver clock card and day-view clock DTO builders."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.domains.dashboard.driver_clocks import (
    DRIVING_LIMIT_H,
    DUTY_LIMIT_H,
    WEEKLY_LIMIT_H,
    audit_to_clock_card,
    build_driver_clock_cards,
    build_driver_day_clocks,
    day_view_as_of,
)
from app.domains.dashboard.schemas import DriverListItemResponse
from app.domains.engine.schemas import DriverTimeline
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = timezone.utc
CHICAGO = "America/Chicago"


def _audit(**overrides: object) -> SimpleNamespace:
    base = {
        "driving_remaining_seconds": 3600.0,
        "duty_window_remaining_seconds": 5 * 3600.0,
        "weekly_hours_used": 60.0,
        "weekly_hours_remaining": 10.0,
        "is_compliant": True,
        "break_required": False,
        "evaluated_at": datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _driver(
    driver_id: str = "d1",
    *,
    is_live: bool = True,
    driver_name: str | None = "Alice",
) -> DriverListItemResponse:
    return DriverListItemResponse(
        driver_id=driver_id,
        driver_name=driver_name,
        tenant_id="t1",
        is_live=is_live,
        event_count=1,
        current_status="D",
    )


def test_audit_to_clock_card_derives_used_and_limits() -> None:
    card = audit_to_clock_card(_driver(), _audit(), today="2026-07-30")

    assert card.clocks["driving"].limit_h == DRIVING_LIMIT_H
    assert card.clocks["driving"].remaining_h == 1.0
    assert card.clocks["driving"].used_h == round(DRIVING_LIMIT_H - 1.0, 2)

    assert card.clocks["duty"].limit_h == DUTY_LIMIT_H
    assert card.clocks["duty"].remaining_h == 5.0

    assert card.clocks["weekly"].limit_h == WEEKLY_LIMIT_H
    assert card.clocks["weekly"].used_h == 60.0
    assert card.clocks["weekly"].remaining_h == 10.0

    assert card.day_href == "/ui/drivers/d1?date=2026-07-30"
    assert card.min_remaining_h == 1.0


def test_build_driver_clock_cards_sorts_by_tightest_remaining() -> None:
    drivers = [
        _driver("loose", driver_name="Loose"),
        _driver("tight", driver_name="Tight"),
        _driver("hist", is_live=False, driver_name="Historical"),
    ]
    audits = {
        "loose": _audit(driving_remaining_seconds=8 * 3600.0),
        "tight": _audit(driving_remaining_seconds=30 * 60.0),
        "hist": _audit(driving_remaining_seconds=0.0),
    }

    cards = build_driver_clock_cards(drivers, audits, today="2026-07-30")

    assert [c.driver_id for c in cards] == ["tight", "loose"]
    assert cards[0].at_risk is True
    assert cards[0].min_remaining_h == 0.5


def test_build_driver_clock_cards_skips_without_audit() -> None:
    drivers = [_driver("no-audit")]
    assert build_driver_clock_cards(drivers, {}, today="2026-07-30") == []


def test_audit_to_clock_card_at_risk_when_non_compliant() -> None:
    card = audit_to_clock_card(
        _driver(),
        _audit(is_compliant=False, driving_remaining_seconds=10 * 3600.0),
        today="2026-07-30",
    )
    assert card.at_risk is True


def _ts(hours: float) -> datetime:
    return datetime(2026, 7, 20, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def test_day_view_as_of_uses_day_end_for_past_dates() -> None:
    past = date(2026, 7, 20)
    as_of = day_view_as_of(past, CHICAGO)
    local = as_of.astimezone(ZoneInfo(CHICAGO))
    assert local.date() == past
    assert local.hour == 23 and local.minute == 59


def test_day_view_as_of_live_for_today() -> None:
    tz = ZoneInfo(CHICAGO)
    today = datetime.now(tz).date()
    as_of = day_view_as_of(today, CHICAGO)
    local = as_of.astimezone(tz)
    assert local.date() == today
    assert as_of <= datetime.now(timezone.utc)


def test_build_driver_day_clocks_shift_start_after_qualifying_rest() -> None:
    """10h OFF then driving → shift start at duty resumption."""
    events = [
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(0),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(10),
        ),
    ]
    as_of = _ts(10 + 3)
    clocks = build_driver_day_clocks(
        driver_id="drv1",
        tenant_id="tenant1",
        events=events,
        as_of=as_of,
        display_tz_name=CHICAGO,
    )

    assert clocks.shift_start_utc == _ts(10)
    assert clocks.shift_start_local is not None
    assert "2026-07-20" in clocks.shift_start_local
    assert clocks.clocks["driving"].used_h == 3.0
    assert clocks.clocks["driving"].remaining_h == round(DRIVING_LIMIT_H - 3.0, 2)
    assert clocks.evaluated_at == as_of
    assert clocks.as_of_local


def test_build_driver_day_clocks_reports_34h_restart() -> None:
    """Long OFF spanning two 1–5 AM periods → valid 34h restart in window."""
    start = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=start),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=start + timedelta(hours=20),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.ON_DUTY.value,
            timestamp=start + timedelta(hours=20 + 36),
        ),
    ]
    as_of = start + timedelta(hours=20 + 36 + 1)
    clocks = build_driver_day_clocks(
        driver_id="drv2",
        tenant_id="tenant1",
        events=events,
        as_of=as_of,
        display_tz_name=CHICAGO,
    )

    assert clocks.had_34h_restart is True
    assert clocks.last_valid_restart_utc is not None
    assert clocks.last_valid_restart_local is not None
    assert clocks.clocks["weekly"].used_h < 5.0
    assert clocks.clocks["weekly"].remaining_h > 65.0
