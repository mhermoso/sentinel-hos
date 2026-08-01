"""Jinja2 + HTMX UI routes for the HOS timeline dashboard."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.domains.dashboard.alert_detail import build_alert_detail, logs_to_events
from app.domains.dashboard.alert_filters import (
    default_alerts_local_range,
    detect_active_range,
    local_dates_to_utc_window,
    normalize_filter_str,
    quick_range_dates,
)
from app.domains.dashboard.driver_names import resolve_driver_name
from app.domains.dashboard.router import (
    _build_driver_day,
    _list_all_drivers,
    get_alerts_dispatch_log,
    get_driver_positions,
    list_fleet_alerts,
)
from app.domains.dashboard.schemas import AlertDetailResponse
from app.domains.dashboard.timezone import (
    DISPLAY_TIMEZONES,
    resolve_display_timezone,
    set_display_timezone_cookie,
    tz_abbreviation,
    zoneinfo_for,
)
from app.domains.ingestion.models import CanonicalHOSLogRecord

logger = logging.getLogger("dcw.dashboard.ui")

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

ui_router = APIRouter(tags=["ui"])


def _today_local(display_tz: str) -> date:
    return datetime.now(zoneinfo_for(display_tz)).date()


def _parse_date(value: Optional[str], display_tz: str) -> date:
    if not value:
        return _today_local(display_tz)
    return date.fromisoformat(value)


def _tz_context(request: Request, tz_param: Optional[str] = None) -> Dict[str, Any]:
    display_tz = resolve_display_timezone(request, tz_param=tz_param)
    return {
        "timezone": display_tz,
        "tz_abbrev": tz_abbreviation(display_tz),
        "display_timezones": DISPLAY_TIMEZONES,
    }


@ui_router.get("/ui", response_class=RedirectResponse, include_in_schema=False)
async def ui_root() -> RedirectResponse:
    return RedirectResponse(url="/ui/home", status_code=302)


@ui_router.get("/ui/preferences/timezone", include_in_schema=False)
async def ui_set_timezone(
    request: Request,
    tz: str = Query(..., description="IANA timezone"),
    next: str = Query(default="/ui/home", alias="next"),
) -> RedirectResponse:
    """Persist display timezone cookie and redirect back."""
    # Prevent open redirects — only allow relative paths
    if not next.startswith("/"):
        next = "/ui/home"
    response = RedirectResponse(url=next, status_code=302)
    set_display_timezone_cookie(response, tz)
    return response


def _parse_optional_local_date(value: Optional[str]) -> Optional[date]:
    cleaned = normalize_filter_str(value)
    if not cleaned:
        return None
    return date.fromisoformat(cleaned)


def _alerts_query_context(
    display_tz: str,
    *,
    severity: Optional[str],
    source: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
    driver_id: Optional[str],
) -> Dict[str, Any]:
    """Normalize filters and apply default last-30d window when dates omitted."""
    severity_n = normalize_filter_str(severity)
    source_n = normalize_filter_str(source)
    driver_n = normalize_filter_str(driver_id)
    from_d = _parse_optional_local_date(from_date)
    to_d = _parse_optional_local_date(to_date)
    if from_d is None and to_d is None:
        from_d, to_d = default_alerts_local_range(display_tz)
    elif from_d is None and to_d is not None:
        from_d = to_d - timedelta(days=29)
    elif to_d is None and from_d is not None:
        to_d = _today_local(display_tz)

    assert from_d is not None and to_d is not None
    from_ts, to_ts = local_dates_to_utc_window(from_d, to_d, display_tz)
    active_range = detect_active_range(from_d, to_d, display_tz)
    range_chips: List[Dict[str, str]] = []
    for key, label in (
        ("7d", "Last 7"),
        ("21d", "Last 21"),
        ("30d", "Last 30"),
        ("current_month", "Current month"),
        ("last_month", "Last month"),
    ):
        start, end = quick_range_dates(key, display_tz)
        range_chips.append(
            {
                "key": key,
                "label": label,
                "from": start.isoformat(),
                "to": end.isoformat(),
            }
        )
    return {
        "severity": severity_n or "",
        "source": source_n or "",
        "driver_id": driver_n or "",
        "from_date": from_d.isoformat(),
        "to_date": to_d.isoformat(),
        "from_ts": from_ts,
        "to_ts": to_ts,
        "severity_filter": severity_n,
        "source_filter": source_n,
        "driver_filter": driver_n,
        "active_range": active_range or "",
        "range_chips": range_chips,
    }


@ui_router.get("/ui/drivers", response_class=HTMLResponse)
async def ui_drivers(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    tz_ctx = _tz_context(request)
    drivers = await _list_all_drivers(session)
    health = await _health_context(session)
    return templates.TemplateResponse(
        request,
        "drivers.html",
        {
            "drivers": drivers,
            "health": health,
            "today": _today_local(tz_ctx["timezone"]).isoformat(),
            "alert_dry_run": settings.ALERT_DRY_RUN,
            "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
            "current_path": str(request.url.path),
            **tz_ctx,
        },
    )


@ui_router.get("/ui/drivers/partial", response_class=HTMLResponse)
async def ui_drivers_partial(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """HTMX fragment: refreshable driver list rows."""
    tz_ctx = _tz_context(request)
    drivers = await _list_all_drivers(session)
    return templates.TemplateResponse(
        request,
        "partials/driver_rows.html",
        {
            "drivers": drivers,
            "today": _today_local(tz_ctx["timezone"]).isoformat(),
        },
    )


@ui_router.get("/ui/drivers/{driver_id}", response_class=HTMLResponse)
async def ui_driver_day(
    request: Request,
    driver_id: str,
    date_str: Optional[str] = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    tz_ctx = _tz_context(request)
    display_tz = tz_ctx["timezone"]
    local_date = _parse_date(date_str, display_tz)
    day = await _build_driver_day(session, driver_id, local_date, display_tz=display_tz)
    health = await _health_context(session)
    prev_date = (local_date - timedelta(days=1)).isoformat()
    next_date = (local_date + timedelta(days=1)).isoformat()
    available_dates = await _available_local_dates(session, driver_id, display_tz)

    return templates.TemplateResponse(
        request,
        "driver_day.html",
        {
            "day": day,
            "health": health,
            "prev_date": prev_date,
            "next_date": next_date,
            "today": _today_local(display_tz).isoformat(),
            "available_dates": available_dates,
            "alert_dry_run": settings.ALERT_DRY_RUN,
            "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
            "day_json": day.model_dump(mode="json"),
            "current_path": str(request.url.path)
            + (f"?date={local_date.isoformat()}" if date_str else ""),
            **tz_ctx,
        },
    )


@ui_router.get("/ui/drivers/{driver_id}/partial", response_class=HTMLResponse)
async def ui_driver_day_partial(
    request: Request,
    driver_id: str,
    date_str: Optional[str] = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """HTMX fragment: refreshable day grid + totals + markers."""
    tz_ctx = _tz_context(request)
    display_tz = tz_ctx["timezone"]
    local_date = _parse_date(date_str, display_tz)
    day = await _build_driver_day(session, driver_id, local_date, display_tz=display_tz)
    return templates.TemplateResponse(
        request,
        "partials/day_grid.html",
        {
            "day": day,
            "day_json": day.model_dump(mode="json"),
            "timezone": display_tz,
        },
    )


@ui_router.get("/ui/alerts", response_class=HTMLResponse)
async def ui_alerts(
    request: Request,
    severity: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    driver_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    tz_ctx = _tz_context(request)
    display_tz = tz_ctx["timezone"]
    zone = zoneinfo_for(display_tz)
    from_ts = None
    to_ts = None
    if from_date:
        from_ts = datetime(int(from_date[0:4]), int(from_date[5:7]), int(from_date[8:10]), tzinfo=zone).astimezone(
            ZoneInfo("UTC")
        )
    if to_date:
        to_end = datetime(int(to_date[0:4]), int(to_date[5:7]), int(to_date[8:10]), tzinfo=zone) + timedelta(days=1)
        to_ts = to_end.astimezone(ZoneInfo("UTC"))

    # Map UI "Alert" label already uses CRITICAL in the select value
    response = await list_fleet_alerts(
        severity=severity,
        from_ts=from_ts,
        to_ts=to_ts,
        driver_id=driver_id or None,
        source=source,
        tz=display_tz,
        session=session,
    )
    health = await _health_context(session)
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {
            "alerts": response.alerts,
            "severity": severity or "",
            "source": source or "",
            "from_date": from_date or "",
            "to_date": to_date or "",
            "driver_id": driver_id or "",
            "health": health,
            "alert_dry_run": settings.ALERT_DRY_RUN,
            "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
            "current_path": str(request.url.path),
            **tz_ctx,
        },
    )


@ui_router.get("/ui/alerts/partial", response_class=HTMLResponse)
async def ui_alerts_partial(
    request: Request,
    severity: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    driver_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    tz_ctx = _tz_context(request)
    display_tz = tz_ctx["timezone"]
    zone = zoneinfo_for(display_tz)
    from_ts = None
    to_ts = None
    if from_date:
        from_ts = datetime(int(from_date[0:4]), int(from_date[5:7]), int(from_date[8:10]), tzinfo=zone).astimezone(
            ZoneInfo("UTC")
        )
    if to_date:
        to_end = datetime(int(to_date[0:4]), int(to_date[5:7]), int(to_date[8:10]), tzinfo=zone) + timedelta(days=1)
        to_ts = to_end.astimezone(ZoneInfo("UTC"))

    response = await list_fleet_alerts(
        severity=severity,
        from_ts=from_ts,
        to_ts=to_ts,
        driver_id=driver_id or None,
        source=source,
        tz=display_tz,
        session=session,
    )
    return templates.TemplateResponse(
        request,
        "partials/alert_rows.html",
        {"alerts": response.alerts},
    )


@ui_router.get("/ui/drivers/{driver_id}/alerts/detail", response_class=HTMLResponse)
async def ui_alert_detail(
    request: Request,
    driver_id: str,
    as_of: str = Query(...),
    violation_type: str = Query(...),
    source: str = Query(default="backtest"),
    description: str = Query(default=""),
    severity: str = Query(default=""),
    rule_ref: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """HTMX fragment: alert calculation drawer body."""
    tz_ctx = _tz_context(request)
    display_tz = tz_ctx["timezone"]
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=ZoneInfo("UTC"))

    tenant_id = settings.GEOTAB_DATABASE
    lookback = settings.WEEKLY_CYCLE_DAYS + 3
    cutoff = as_of_dt - timedelta(days=lookback)
    stmt = (
        select(CanonicalHOSLogRecord)
        .where(
            CanonicalHOSLogRecord.tenant_id == tenant_id,
            CanonicalHOSLogRecord.driver_id == driver_id,
            CanonicalHOSLogRecord.event_timestamp >= cutoff,
            CanonicalHOSLogRecord.event_timestamp <= as_of_dt,
        )
        .order_by(CanonicalHOSLogRecord.event_timestamp.asc())
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())
    db_name = next((r.driver_name for r in records if r.driver_name), None)
    driver_name = resolve_driver_name(driver_id, db_name)
    events = logs_to_events(records)
    detail = build_alert_detail(
        driver_id=driver_id,
        tenant_id=tenant_id,
        driver_name=driver_name,
        events=events,
        as_of=as_of_dt,
        violation_type=violation_type,
        source=source,
        display_tz_name=display_tz,
        description_hint=description,
        severity_hint=severity,
        rule_ref_hint=rule_ref,
    )
    detail_model = AlertDetailResponse.model_validate(detail)
    detail_json = detail_model.model_dump(mode="json")
    return templates.TemplateResponse(
        request,
        "partials/alert_detail.html",
        {
            "detail": detail_json,
            "timezone": display_tz,
            "tz_abbrev": tz_ctx["tz_abbrev"],
        },
    )


async def _health_context(session: AsyncSession) -> Dict[str, Any]:
    db_status = "unknown"
    redis_status = "unknown"
    try:
        await session.execute(select(func.now()))
        db_status = "healthy"
    except Exception as exc:
        logger.error("UI health DB check failed: %s", exc)
        db_status = "unhealthy"
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        await redis.ping()
        redis_status = "healthy"
    except Exception as exc:
        logger.error("UI health Redis check failed: %s", exc)
        redis_status = "unhealthy"
    return {
        "status": "healthy" if db_status == "healthy" and redis_status == "healthy" else "degraded",
        "database": db_status,
        "redis": redis_status,
        "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
        "alert_dry_run": settings.ALERT_DRY_RUN,
    }


async def _available_local_dates(
    session: AsyncSession,
    driver_id: str,
    display_tz: str,
) -> list[str]:
    """Return distinct local dates that have HOS activity for the driver."""
    tenant_id = settings.GEOTAB_DATABASE
    stmt = (
        select(CanonicalHOSLogRecord.event_timestamp)
        .where(
            CanonicalHOSLogRecord.tenant_id == tenant_id,
            CanonicalHOSLogRecord.driver_id == driver_id,
        )
        .order_by(CanonicalHOSLogRecord.event_timestamp.asc())
    )
    result = await session.execute(stmt)
    timestamps = list(result.scalars().all())
    if not timestamps:
        return []

    zone = zoneinfo_for(display_tz)
    dates: set[date] = set()
    for ts in timestamps:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        dates.add(ts.astimezone(zone).date())

    if dates:
        lo, hi = min(dates), max(dates)
        hi = hi + timedelta(days=1)
        cursor = lo
        while cursor <= hi:
            dates.add(cursor)
            cursor += timedelta(days=1)

    return [d.isoformat() for d in sorted(dates)]
