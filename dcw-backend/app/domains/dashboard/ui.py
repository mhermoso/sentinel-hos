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
from app.domains.dashboard.driver_clocks import (
    DriverDayClocks,
    build_driver_day_clocks,
    day_view_as_of,
)
from app.domains.dashboard.driver_filters import filter_drivers
from app.domains.dashboard.driver_names import resolve_driver_name
from app.domains.dashboard.route_builder import build_day_route_payload
from app.domains.dashboard.ops_feed import (
    LogFilter,
    infer_worker_status,
    merge_feed_rows,
    rows_from_alerts,
    rows_from_audit,
    rows_from_ingestion,
    rows_from_ops,
)
from app.domains.dashboard.router import (
    _build_driver_day,
    _list_all_drivers,
    get_driver_positions,
    get_recent_ingestion,
    list_audit_records,
    list_fleet_alerts,
)
from app.domains.dashboard.schemas import AlertDetailResponse, DriverDayRouteResponse
from app.domains.dashboard.timezone import (
    DISPLAY_TIMEZONES,
    format_display_clock,
    format_display_date,
    format_display_datetime,
    resolve_display_timezone,
    set_display_timezone_cookie,
    tz_abbreviation,
    zoneinfo_for,
)
from app.domains.engine.repository import EngineRepository
from app.domains.ingestion.models import CanonicalHOSLogRecord
from app.domains.ingestion.repository import IngestionRepository

logger = logging.getLogger("dcw.dashboard.ui")

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.filters["local_dt"] = format_display_datetime
templates.env.filters["local_date"] = format_display_date
templates.env.filters["local_clock"] = format_display_clock

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


@ui_router.get("/ui/home", response_class=HTMLResponse)
async def ui_home(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Fleet home: map, 30d warning/violation summary, live Geotab feed."""
    tz_ctx = _tz_context(request)
    display_tz = tz_ctx["timezone"]
    from_d, to_d = default_alerts_local_range(display_tz)
    from_ts, to_ts = local_dates_to_utc_window(from_d, to_d, display_tz)

    positions_resp = await get_driver_positions(session=session)
    alerts_resp = await list_fleet_alerts(
        severity=None,
        from_ts=from_ts,
        to_ts=to_ts,
        driver_id=None,
        source=None,
        tz=display_tz,
        session=session,
    )

    warning_count = sum(
        1 for a in alerts_resp.alerts if str(a.severity).upper() == "WARNING"
    )
    violation_count = sum(
        1 for a in alerts_resp.alerts if str(a.severity).upper() == "VIOLATION"
    )
    positioned_ids = {p.driver_id for p in positions_resp.positions}
    all_drivers = await _list_all_drivers(session)
    no_location = [d for d in all_drivers if d.driver_id not in positioned_ids]
    positions_json = [p.model_dump(mode="json") for p in positions_resp.positions]
    health = await _health_context(session)
    feed_resp = await get_recent_ingestion(limit=20, session=session)
    feed_newest_raw_id = feed_resp.events[0].raw_id if feed_resp.events else ""

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "positions": positions_resp.positions,
            "positions_json": positions_json,
            "no_location": no_location,
            "warning_count": warning_count,
            "violation_count": violation_count,
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "today": _today_local(display_tz).isoformat(),
            "feed_events": feed_resp.events,
            "feed_newest_raw_id": feed_newest_raw_id,
            "health": health,
            "alert_dry_run": settings.ALERT_DRY_RUN,
            "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
            "current_path": str(request.url.path),
            **tz_ctx,
        },
    )


@ui_router.get("/ui/home/feed", response_class=HTMLResponse)
async def ui_home_feed(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """HTMX partial: live Geotab ingestion feed (newest by ingested_at)."""
    resp = await get_recent_ingestion(limit=limit, session=session)
    newest_raw_id = resp.events[0].raw_id if resp.events else ""
    tz_ctx = _tz_context(request)
    return templates.TemplateResponse(
        request,
        "partials/geotab_feed.html",
        {
            "events": resp.events,
            "newest_raw_id": newest_raw_id,
            **tz_ctx,
        },
    )


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


def _drivers_query_context(
    *,
    q: Optional[str],
    status: Optional[str],
    mode: Optional[str],
) -> Dict[str, Any]:
    """Normalize driver list filters for templates and filter_drivers."""
    q_n = normalize_filter_str(q)
    status_n = normalize_filter_str(status)
    mode_n = normalize_filter_str(mode)
    filtered = any(x is not None for x in (q_n, status_n, mode_n))
    return {
        "q": q_n or "",
        "status": status_n or "",
        "mode": mode_n or "",
        "q_filter": q_n,
        "status_filter": status_n,
        "mode_filter": mode_n,
        "filtered": filtered,
    }


async def _drivers_page_context(
    session: AsyncSession,
    *,
    q: Optional[str],
    status: Optional[str],
    mode: Optional[str],
    display_tz: str,
) -> Dict[str, Any]:
    """Shared drivers list filter context."""
    filt = _drivers_query_context(q=q, status=status, mode=mode)
    all_drivers = await _list_all_drivers(session)
    drivers = filter_drivers(
        all_drivers,
        q=filt["q_filter"],
        status=filt["status_filter"],
        mode=filt["mode_filter"],
    )
    today = _today_local(display_tz).isoformat()
    return {
        "drivers": drivers,
        "total_drivers": len(all_drivers),
        "today": today,
        **filt,
    }


async def _build_day_clocks_for_driver(
    session: AsyncSession,
    driver_id: str,
    local_date: date,
    display_tz: str,
) -> Optional[DriverDayClocks]:
    """Fetch lookback logs and recompute day-view clocks at the day as-of instant."""
    as_of = day_view_as_of(local_date, display_tz)
    lookback = settings.WEEKLY_CYCLE_DAYS + 3
    cutoff = as_of - timedelta(days=lookback)
    tenant_id = settings.GEOTAB_DATABASE
    stmt = (
        select(CanonicalHOSLogRecord)
        .where(
            CanonicalHOSLogRecord.tenant_id == tenant_id,
            CanonicalHOSLogRecord.driver_id == driver_id,
            CanonicalHOSLogRecord.event_timestamp >= cutoff,
            CanonicalHOSLogRecord.event_timestamp <= as_of,
        )
        .order_by(CanonicalHOSLogRecord.event_timestamp.asc())
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())
    if not records:
        return None
    events = logs_to_events(records)
    profile = await EngineRepository(session).get_driver_profile(tenant_id, driver_id)
    return build_driver_day_clocks(
        driver_id=driver_id,
        tenant_id=tenant_id,
        events=events,
        as_of=as_of,
        display_tz_name=display_tz,
        profile=profile,
    )


@ui_router.get("/ui/drivers", response_class=HTMLResponse)
async def ui_drivers(
    request: Request,
    q: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    mode: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    tz_ctx = _tz_context(request)
    ctx = await _drivers_page_context(
        session,
        q=q,
        status=status,
        mode=mode,
        display_tz=tz_ctx["timezone"],
    )
    health = await _health_context(session)
    return templates.TemplateResponse(
        request,
        "drivers.html",
        {
            **ctx,
            "health": health,
            "alert_dry_run": settings.ALERT_DRY_RUN,
            "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
            "current_path": str(request.url.path),
            **tz_ctx,
        },
    )


@ui_router.get("/ui/drivers/partial", response_class=HTMLResponse)
async def ui_drivers_partial(
    request: Request,
    q: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    mode: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """HTMX fragment: refreshable driver list rows."""
    tz_ctx = _tz_context(request)
    ctx = await _drivers_page_context(
        session,
        q=q,
        status=status,
        mode=mode,
        display_tz=tz_ctx["timezone"],
    )
    return templates.TemplateResponse(
        request,
        "partials/drivers_refresh.html",
        {**ctx, **tz_ctx},
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
    day_clocks = await _build_day_clocks_for_driver(
        session, driver_id, local_date, display_tz
    )
    health = await _health_context(session)
    prev_date = (local_date - timedelta(days=1)).isoformat()
    next_date = (local_date + timedelta(days=1)).isoformat()
    available_dates = await _available_local_dates(session, driver_id, display_tz)

    return templates.TemplateResponse(
        request,
        "driver_day.html",
        {
            "day": day,
            "day_clocks": day_clocks,
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
    """HTMX fragment: refreshable day clocks + grid + totals + markers."""
    tz_ctx = _tz_context(request)
    display_tz = tz_ctx["timezone"]
    local_date = _parse_date(date_str, display_tz)
    day = await _build_driver_day(session, driver_id, local_date, display_tz=display_tz)
    day_clocks = await _build_day_clocks_for_driver(
        session, driver_id, local_date, display_tz
    )
    return templates.TemplateResponse(
        request,
        "partials/day_grid.html",
        {
            "day": day,
            "day_clocks": day_clocks,
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
    q = _alerts_query_context(
        display_tz,
        severity=severity,
        source=source,
        from_date=from_date,
        to_date=to_date,
        driver_id=driver_id,
    )
    response = await list_fleet_alerts(
        severity=q["severity_filter"],
        from_ts=q["from_ts"],
        to_ts=q["to_ts"],
        driver_id=q["driver_filter"],
        source=q["source_filter"],
        tz=display_tz,
        session=session,
    )
    health = await _health_context(session)
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {
            "alerts": response.alerts,
            "severity": q["severity"],
            "source": q["source"],
            "from_date": q["from_date"],
            "to_date": q["to_date"],
            "driver_id": q["driver_id"],
            "active_range": q["active_range"],
            "range_chips": q["range_chips"],
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
    q = _alerts_query_context(
        display_tz,
        severity=severity,
        source=source,
        from_date=from_date,
        to_date=to_date,
        driver_id=driver_id,
    )
    response = await list_fleet_alerts(
        severity=q["severity_filter"],
        from_ts=q["from_ts"],
        to_ts=q["to_ts"],
        driver_id=q["driver_filter"],
        source=q["source_filter"],
        tz=display_tz,
        session=session,
    )
    return templates.TemplateResponse(
        request,
        "partials/alert_rows.html",
        {"alerts": response.alerts, **tz_ctx},
    )


async def _alert_detail_page_context(
    *,
    request: Request,
    driver_id: str,
    as_of: str,
    violation_type: str,
    source: str,
    description: str,
    severity: str,
    rule_ref: str,
    session: AsyncSession,
) -> Dict[str, Any]:
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
    profile = await EngineRepository(session).get_driver_profile(tenant_id, driver_id)
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
        profile=profile,
    )
    detail_model = AlertDetailResponse.model_validate(detail)
    detail_json = detail_model.model_dump(mode="json")
    return {
        "detail": detail_json,
        "timezone": display_tz,
        "tz_abbrev": tz_ctx["tz_abbrev"],
        "as_of_query": as_of_dt.isoformat(),
        "query": {
            "as_of": as_of_dt.isoformat(),
            "violation_type": violation_type,
            "source": source,
            "description": description,
            "severity": severity,
            "rule_ref": rule_ref,
        },
        **tz_ctx,
    }


async def _build_route_context(
    session: AsyncSession,
    driver_id: str,
    local_date: date,
    display_tz: str,
) -> Dict[str, Any]:
    """Build day route payload for UI partial / full page."""
    day = await _build_driver_day(session, driver_id, local_date, display_tz=display_tz)
    repo = IngestionRepository(session)
    crumbs = await repo.get_gps_breadcrumbs_for_driver_day_route(
        tenant_id=settings.GEOTAB_DATABASE,
        driver_id=driver_id,
        start_utc=day.day_start_utc,
        end_utc=day.day_end_utc,
    )
    breadcrumb_dicts = [
        {
            "event_timestamp": c.event_timestamp,
            "latitude": c.latitude,
            "longitude": c.longitude,
        }
        for c in crumbs
    ]
    hos_events = [
        {
            "event_timestamp": e.event_timestamp,
            "status": e.status,
            "latitude": e.latitude,
            "longitude": e.longitude,
        }
        for e in day.events
    ]
    payload = build_day_route_payload(
        driver_id=driver_id,
        local_date=local_date,
        breadcrumbs=breadcrumb_dicts,
        hos_events=hos_events,
        alert_markers=[m.model_dump() for m in day.alert_markers],
        carry_forward_status=day.carry_forward_status,
    )
    route = DriverDayRouteResponse.model_validate(payload)
    return {
        "route": route,
        "route_json": route.model_dump(mode="json"),
        "day": day,
    }


@ui_router.get("/ui/drivers/{driver_id}/route/detail", response_class=HTMLResponse)
async def ui_route_map_detail(
    request: Request,
    driver_id: str,
    date_str: Optional[str] = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """HTMX fragment: route map drawer body."""
    tz_ctx = _tz_context(request)
    display_tz = tz_ctx["timezone"]
    local_date = _parse_date(date_str, display_tz)
    ctx = await _build_route_context(session, driver_id, local_date, display_tz)
    return templates.TemplateResponse(
        request,
        "partials/route_map_detail.html",
        {**ctx, **tz_ctx, "full_page": False},
    )


@ui_router.get("/ui/drivers/{driver_id}/route", response_class=HTMLResponse)
async def ui_route_map_page(
    request: Request,
    driver_id: str,
    date_str: Optional[str] = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Full-page route map view (same content as the drawer)."""
    tz_ctx = _tz_context(request)
    display_tz = tz_ctx["timezone"]
    local_date = _parse_date(date_str, display_tz)
    ctx = await _build_route_context(session, driver_id, local_date, display_tz)
    health = await _health_context(session)
    return templates.TemplateResponse(
        request,
        "route_map_page.html",
        {
            **ctx,
            **tz_ctx,
            "full_page": True,
            "health": health,
            "alert_dry_run": settings.ALERT_DRY_RUN,
            "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
            "current_path": str(request.url.path),
        },
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
    ctx = await _alert_detail_page_context(
        request=request,
        driver_id=driver_id,
        as_of=as_of,
        violation_type=violation_type,
        source=source,
        description=description,
        severity=severity,
        rule_ref=rule_ref,
        session=session,
    )
    return templates.TemplateResponse(
        request,
        "partials/alert_detail.html",
        {**ctx, "full_page": False},
    )


@ui_router.get("/ui/drivers/{driver_id}/alerts/view", response_class=HTMLResponse)
async def ui_alert_detail_page(
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
    """Full-page alert calculation view (same content as the drawer)."""
    ctx = await _alert_detail_page_context(
        request=request,
        driver_id=driver_id,
        as_of=as_of,
        violation_type=violation_type,
        source=source,
        description=description,
        severity=severity,
        rule_ref=rule_ref,
        session=session,
    )
    health = await _health_context(session)
    return templates.TemplateResponse(
        request,
        "alert_detail_page.html",
        {
            **ctx,
            "full_page": True,
            "health": health,
            "alert_dry_run": settings.ALERT_DRY_RUN,
            "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
            "current_path": str(request.url.path),
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


_LOG_FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "All"),
    ("system", "System"),
    ("ingestion", "Ingestion"),
    ("alerts", "Alerts"),
    ("engine", "Engine"),
)


def _normalize_log_filter(value: Optional[str]) -> LogFilter:
    allowed = {key for key, _ in _LOG_FILTERS}
    if value and value in allowed:
        return value  # type: ignore[return-value]
    return "all"


async def _build_logs_context(
    session: AsyncSession,
    *,
    source: LogFilter,
    limit: int,
) -> Dict[str, Any]:
    """Assemble health + merged activity rows for the Logs page / partial."""
    ops_rows = rows_from_ops(limit=150)
    alert_rows = rows_from_alerts(limit=50)
    feed_resp = await get_recent_ingestion(limit=40, session=session)
    ingestion_rows = rows_from_ingestion(list(feed_resp.events))
    audit_resp = await list_audit_records(
        driver_id=None,
        limit=40,
        offset=0,
        session=session,
    )
    engine_rows = rows_from_audit(list(audit_resp.records))
    rows = merge_feed_rows(
        ops_rows,
        alert_rows,
        ingestion_rows,
        engine_rows,
        source_filter=source,
        limit=limit,
    )
    health = await _health_context(session)
    worker = infer_worker_status(ops_rows + ingestion_rows)
    return {
        "rows": rows,
        "source": source,
        "filter_chips": [{"key": k, "label": label} for k, label in _LOG_FILTERS],
        "services": {
            "api": {"status": "healthy", "label": "API", "detail": "Serving"},
            "database": {
                "status": health["database"],
                "label": "Postgres",
                "detail": health["database"],
            },
            "redis": {
                "status": health["redis"],
                "label": "Redis",
                "detail": health["redis"],
            },
            "worker": worker,
        },
        "health": health,
        "row_count": len(rows),
    }


@ui_router.get("/ui/logs", response_class=HTMLResponse)
async def ui_logs(
    request: Request,
    source: Optional[str] = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=300),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Ops Logs: service health + live pipeline activity feed."""
    tz_ctx = _tz_context(request)
    filt = _normalize_log_filter(source)
    ctx = await _build_logs_context(session, source=filt, limit=limit)
    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            **ctx,
            **tz_ctx,
            "alert_dry_run": settings.ALERT_DRY_RUN,
            "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
            "current_path": str(request.url.path),
        },
    )


@ui_router.get("/ui/logs/partial", response_class=HTMLResponse)
async def ui_logs_partial(
    request: Request,
    source: Optional[str] = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=300),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """HTMX partial: refreshable Logs health strip + activity table body."""
    tz_ctx = _tz_context(request)
    filt = _normalize_log_filter(source)
    ctx = await _build_logs_context(session, source=filt, limit=limit)
    return templates.TemplateResponse(
        request,
        "partials/logs_feed.html",
        {
            **ctx,
            **tz_ctx,
        },
    )


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
