"""Build HOS clock gauges from audit records or day-view engine recompute."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.security import compute_inputs_hash
from app.domains.dashboard.day_builder import chicago_day_bounds
from app.domains.dashboard.schemas import DriverListItemResponse
from app.domains.dashboard.timezone import zoneinfo_for
from app.domains.engine.calculators import MAX_DRIVING_SECONDS, MAX_DUTY_WINDOW_SECONDS
from app.domains.engine.models import AuditRecord
from app.domains.engine.replay import (
    compute_weekly_duty_seconds,
    find_restart_reset_point,
    truncate_timeline_to,
)
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverProfile, DriverTimeline
from app.domains.engine.state_machine import run_state_machine

AT_RISK_REMAINING_HOURS = 2.0

DRIVING_LIMIT_H = MAX_DRIVING_SECONDS / 3600.0
DUTY_LIMIT_H = MAX_DUTY_WINDOW_SECONDS / 3600.0
WEEKLY_LIMIT_H = settings.WEEKLY_CYCLE_LIMIT_HOURS


class ClockGauge(BaseModel):
    """Single limit gauge for server-rendered bar markup."""

    model_config = ConfigDict(frozen=True)

    label: str
    used_h: float
    limit_h: float
    remaining_h: float
    pct: float
    over: bool


class DriverDayClocks(BaseModel):
    """HOS clocks for a driver day view, recomputed at ``as_of``."""

    model_config = ConfigDict(frozen=True)

    clocks: dict[str, ClockGauge]
    shift_start_utc: Optional[datetime] = None
    shift_start_local: Optional[str] = None
    had_34h_restart: bool = False
    last_valid_restart_utc: Optional[datetime] = None
    last_valid_restart_local: Optional[str] = None
    break_required: bool = False
    is_compliant: bool = True
    evaluated_at: datetime
    as_of_local: str
    at_risk: bool = False


class DriverClockCard(BaseModel):
    """Compact per-driver clock strip card."""

    model_config = ConfigDict(frozen=True)

    driver_id: str
    driver_name: Optional[str] = None
    status: Optional[str] = None
    is_compliant: bool = True
    break_required: bool = False
    evaluated_at: datetime
    clocks: dict[str, ClockGauge]
    min_remaining_h: float = Field(description="Sort key: tightest remaining clock")
    at_risk: bool = Field(description="Any remaining < 2h or non-compliant")
    day_href: str


def _hours(seconds: float) -> float:
    return round(seconds / 3600.0, 2)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _local_label(dt: datetime, tz: ZoneInfo) -> str:
    return _ensure_utc(dt).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def day_view_as_of(local_date: date, display_tz: str) -> datetime:
    """Evaluation instant for a day grid: end-of-day for past dates, live for today."""
    tz = zoneinfo_for(display_tz)
    bounds = chicago_day_bounds(local_date, tz)
    day_end = bounds.end_utc - timedelta(seconds=1)
    today = datetime.now(tz).date()
    if local_date < today:
        return day_end
    now = datetime.now(timezone.utc)
    return min(now, day_end)


def _build_gauge(*, label: str, used_h: float, limit_h: float, remaining_h: float) -> ClockGauge:
    remaining_h = round(max(0.0, remaining_h), 2)
    used_h = round(max(0.0, used_h), 2)
    limit_h = round(limit_h, 2)
    pct = min(100.0, (used_h / limit_h) * 100.0) if limit_h > 0 else 0.0
    over = used_h >= limit_h or remaining_h <= 0
    return ClockGauge(
        label=label,
        used_h=used_h,
        limit_h=limit_h,
        remaining_h=remaining_h,
        pct=round(pct, 1),
        over=over,
    )


def build_driver_day_clocks(
    *,
    driver_id: str,
    tenant_id: str,
    events: Sequence[DriverTimeline.HOSEvent],
    as_of: datetime,
    display_tz_name: str,
    profile: DriverProfile | None = None,
) -> DriverDayClocks:
    """Recompute HOS gauges + shift/restart context at ``as_of`` (no violation matching)."""
    as_of = _ensure_utc(as_of)
    tz = zoneinfo_for(display_tz_name)
    home_tz = ZoneInfo(settings.DEFAULT_HOME_TERMINAL_TIMEZONE)
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
        home_terminal_tz=home_tz,
    )
    reset_point = find_restart_reset_point(
        truncated.events,
        as_of,
        home_terminal_tz=home_tz,
    )
    inputs_hash = compute_inputs_hash(
        {
            "tenant_id": tenant_id,
            "driver_id": driver_id,
            "as_of": as_of.isoformat(),
            "event_count": len(truncated.events),
            "purpose": "day_clocks",
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

    shift_start = state.current_shift.shift_start if state.current_shift else None
    restart_at = state.last_valid_restart_at if state.last_valid_restart_at else reset_point
    had_restart = bool(state.had_34h_restart or reset_point)

    driving_remaining_h = _hours(result.driving_remaining_seconds)
    duty_remaining_h = _hours(result.duty_window_remaining_seconds)
    weekly_remaining_h = round(result.weekly_hours_remaining, 2)
    weekly_used_h = round(result.weekly_hours_used, 2)
    driving_used_h = max(0.0, DRIVING_LIMIT_H - driving_remaining_h)
    duty_used_h = max(0.0, DUTY_LIMIT_H - duty_remaining_h)

    clocks = {
        "driving": _build_gauge(
            label="Driving",
            used_h=driving_used_h,
            limit_h=DRIVING_LIMIT_H,
            remaining_h=driving_remaining_h,
        ),
        "duty": _build_gauge(
            label="Duty",
            used_h=duty_used_h,
            limit_h=DUTY_LIMIT_H,
            remaining_h=duty_remaining_h,
        ),
        "weekly": _build_gauge(
            label="Weekly",
            used_h=weekly_used_h,
            limit_h=WEEKLY_LIMIT_H,
            remaining_h=weekly_remaining_h,
        ),
    }

    remainings = [driving_remaining_h, duty_remaining_h, weekly_remaining_h]
    at_risk = (not result.is_compliant) or any(r < AT_RISK_REMAINING_HOURS for r in remainings)

    return DriverDayClocks(
        clocks=clocks,
        shift_start_utc=shift_start,
        shift_start_local=_local_label(shift_start, tz) if shift_start else None,
        had_34h_restart=had_restart,
        last_valid_restart_utc=restart_at,
        last_valid_restart_local=_local_label(restart_at, tz) if restart_at else None,
        break_required=result.break_required,
        is_compliant=result.is_compliant,
        evaluated_at=as_of,
        as_of_local=_local_label(as_of, tz),
        at_risk=at_risk,
    )


def audit_to_clock_card(
    driver: DriverListItemResponse,
    audit: AuditRecord,
    *,
    today: str,
) -> DriverClockCard:
    """Map a latest audit + driver row into a clock card."""
    driving_remaining_h = _hours(audit.driving_remaining_seconds)
    duty_remaining_h = _hours(audit.duty_window_remaining_seconds)
    weekly_remaining_h = round(max(0.0, audit.weekly_hours_remaining), 2)
    weekly_used_h = round(max(0.0, audit.weekly_hours_used), 2)

    driving_used_h = max(0.0, DRIVING_LIMIT_H - driving_remaining_h)
    duty_used_h = max(0.0, DUTY_LIMIT_H - duty_remaining_h)

    clocks = {
        "driving": _build_gauge(
            label="Driving",
            used_h=driving_used_h,
            limit_h=DRIVING_LIMIT_H,
            remaining_h=driving_remaining_h,
        ),
        "duty": _build_gauge(
            label="Duty",
            used_h=duty_used_h,
            limit_h=DUTY_LIMIT_H,
            remaining_h=duty_remaining_h,
        ),
        "weekly": _build_gauge(
            label="Weekly",
            used_h=weekly_used_h,
            limit_h=WEEKLY_LIMIT_H,
            remaining_h=weekly_remaining_h,
        ),
    }

    remainings = [driving_remaining_h, duty_remaining_h, weekly_remaining_h]
    min_remaining_h = min(remainings)
    at_risk = (not audit.is_compliant) or any(r < AT_RISK_REMAINING_HOURS for r in remainings)

    return DriverClockCard(
        driver_id=driver.driver_id,
        driver_name=driver.driver_name,
        status=driver.current_status,
        is_compliant=audit.is_compliant,
        break_required=audit.break_required,
        evaluated_at=audit.evaluated_at,
        clocks=clocks,
        min_remaining_h=min_remaining_h,
        at_risk=at_risk,
        day_href=f"/ui/drivers/{driver.driver_id}?date={today}",
    )


def build_driver_clock_cards(
    drivers: list[DriverListItemResponse],
    audits_by_driver: dict[str, AuditRecord],
    *,
    today: str,
) -> list[DriverClockCard]:
    """Build sorted clock cards for live drivers that have a latest audit."""
    cards: list[DriverClockCard] = []
    for driver in drivers:
        if not driver.is_live:
            continue
        audit = audits_by_driver.get(driver.driver_id)
        if audit is None:
            continue
        cards.append(audit_to_clock_card(driver, audit, today=today))
    cards.sort(key=lambda c: c.min_remaining_h)
    return cards
