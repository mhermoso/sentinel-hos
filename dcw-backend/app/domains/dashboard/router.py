"""FastAPI router for the DCW dashboard API.

Provides REST endpoints for:
  - GET /api/health            — extended health check
  - GET /api/drivers           — all drivers (historical + live)
  - GET /api/drivers/active    — live driver statuses from Redis + PG
  - GET /api/drivers/positions — latest lat/lon per driver (+ 30d W/V)
  - GET /api/drivers/{id}/day  — home-terminal day grid + alert markers
  - GET /api/drivers/{id}/day/route — GPS trail + status-colored segments
  - GET /api/units/{device_id}/day/route — unit-day GPS trail + status segments
  - GET /api/alerts            — fleet alerts (backtest + live) with filters
  - GET /api/alerts/dispatch-log — Twilio / dry-run JSONL history
  - GET /api/ops/log           — dcw.* ops JSONL event history
  - GET /api/ingestion/recent  — newest Geotab HOS logs by ingested_at
  - GET /api/drivers/{id}/alert-markers — merged backtest + live markers
  - GET /api/drivers/{id}/timeline   — historical HOS log query
  - GET /api/drivers/{id}/compliance — latest compliance result
  - GET /api/audit/records     — paginated audit record listing
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.ops_log import read_ops_log
from app.core.redis import get_redis
from app.domains.dashboard.alert_detail import build_alert_detail, logs_to_events
from app.domains.dashboard.alert_filters import (
    default_alerts_utc_window,
    normalize_filter_str,
)
from app.domains.dashboard.day_builder import (
    METERS_PER_MILE,
    RawHOSEvent,
    annotate_marker_hours,
    attach_alerts_to_segments,
    build_day_points,
    chicago_day_bounds,
    collect_fleet_alerts,
    filter_backtest_markers,
    format_distance_mi,
    format_duration_hhmm,
    load_backtest_dispatches,
    markers_from_audit_violations,
    merge_alert_markers,
)
from app.domains.dashboard.driver_filters import filter_drivers
from app.domains.dashboard.driver_names import resolve_driver_name
from app.domains.dashboard.fleet_select import resolve_fleet
from app.domains.dashboard.route_builder import build_day_route_payload
from app.domains.dashboard.schemas import (
    AlertDetailResponse,
    AlertMarkerResponse,
    AlertMarkersResponse,
    AuditRecordResponse,
    ComplianceSnapshotResponse,
    DayStatusEventResponse,
    DispatchLogItemResponse,
    DispatchLogResponse,
    DriverDayResponse,
    DriverDayRouteResponse,
    DriverListItemResponse,
    DriverListResponse,
    DriverPositionResponse,
    DriverPositionsResponse,
    DriverStatusResponse,
    DriverTimelineResponse,
    DurationTotalsResponse,
    FleetAlertItemResponse,
    FleetAlertsResponse,
    HealthResponse,
    HOSEventResponse,
    OpsLogItemResponse,
    OpsLogResponse,
    PaginatedAuditResponse,
    RecentIngestionItemResponse,
    RecentIngestionResponse,
    ViolationResponse,
)
from app.domains.dashboard.timezone import default_display_timezone, zoneinfo_for
from app.domains.engine.models import AuditRecord
from app.domains.engine.repository import EngineRepository
from app.domains.ingestion.models import CanonicalHOSLogRecord
from app.domains.ingestion.repository import IngestionRepository
from app.domains.ingestion.roster import is_unassigned_driver_id
from app.domains.ingestion.roster_repository import RosterRepository
from app.domains.ingestion.schemas import DriverRosterEntry
from app.domains.notifier.alert_logger import read_alert_log

logger = logging.getLogger("dcw.dashboard.router")

router = APIRouter(prefix="/api", tags=["dashboard"])


# ── Shared helpers (also used by UI routes) ───────────────────────────────


def _is_geotab_tenant(tenant_id: str) -> bool:
    """Backtest dispatches are Geotab-scoped only."""
    return bool(settings.GEOTAB_DATABASE) and tenant_id == settings.GEOTAB_DATABASE


def _position_roster_fields(
    driver_id: str,
    roster_by_id: dict[str, DriverRosterEntry],
) -> tuple[bool | None, str | None]:
    """Return ``(has_unit_assignment, unit_label)`` for a map position row.

    Unassigned HOS sentinels are never "on a unit". Missing roster rows leave
    both fields ``None`` so the client can treat them as off-unit under the
    default filter.
    """
    if is_unassigned_driver_id(driver_id):
        return False, None
    roster = roster_by_id.get(driver_id)
    if roster is None:
        return None, None
    return bool(roster.has_unit_assignment), roster.unit_label


async def _list_all_drivers(
    session: AsyncSession,
    tenant_id: str,
) -> list[DriverListItemResponse]:
    """Union of distinct PG drivers + Redis active set + roster people."""
    active_ids = set(await IngestionRepository.get_active_driver_ids(tenant_id))
    roster_by_id = await RosterRepository(session).map_by_external_id(tenant_id)

    stmt = (
        select(
            CanonicalHOSLogRecord.driver_id,
            func.max(CanonicalHOSLogRecord.driver_name).label("driver_name"),
            func.count().label("event_count"),
            func.min(CanonicalHOSLogRecord.event_timestamp).label("first_event_at"),
            func.max(CanonicalHOSLogRecord.event_timestamp).label("last_event_at"),
        )
        .where(CanonicalHOSLogRecord.tenant_id == tenant_id)
        .group_by(CanonicalHOSLogRecord.driver_id)
        .order_by(func.max(CanonicalHOSLogRecord.driver_name).nulls_last(), CanonicalHOSLogRecord.driver_id)
    )
    result = await session.execute(stmt)
    rows = list(result.all())

    by_id: dict[str, DriverListItemResponse] = {}
    for row in rows:
        # Latest status at last_event_at
        status_stmt = (
            select(CanonicalHOSLogRecord.status)
            .where(
                CanonicalHOSLogRecord.tenant_id == tenant_id,
                CanonicalHOSLogRecord.driver_id == row.driver_id,
                CanonicalHOSLogRecord.event_timestamp == row.last_event_at,
            )
            .limit(1)
        )
        status_result = await session.execute(status_stmt)
        current_status = status_result.scalar_one_or_none()
        roster = roster_by_id.get(row.driver_id)

        by_id[row.driver_id] = DriverListItemResponse(
            driver_id=row.driver_id,
            driver_name=resolve_driver_name(
                row.driver_id,
                roster.display_name if roster and roster.display_name else row.driver_name,
            ),
            tenant_id=tenant_id,
            is_live=row.driver_id in active_ids,
            event_count=int(row.event_count),
            first_event_at=row.first_event_at,
            last_event_at=row.last_event_at,
            current_status=current_status,
            roster_active=roster.is_active if roster else None,
            profile_complete=roster.profile_complete if roster else None,
            has_unit_assignment=roster.has_unit_assignment if roster else None,
            unit_label=roster.unit_label if roster else None,
        )

    for driver_id in active_ids:
        if driver_id in by_id:
            continue
        roster = roster_by_id.get(driver_id)
        by_id[driver_id] = DriverListItemResponse(
            driver_id=driver_id,
            driver_name=resolve_driver_name(
                driver_id,
                roster.display_name if roster else None,
            ),
            tenant_id=tenant_id,
            is_live=True,
            event_count=0,
            roster_active=roster.is_active if roster else None,
            profile_complete=roster.profile_complete if roster else None,
            has_unit_assignment=roster.has_unit_assignment if roster else None,
            unit_label=roster.unit_label if roster else None,
        )

    # Include roster people who have no HOS events yet (Assigned view).
    for external_id, roster in roster_by_id.items():
        if external_id in by_id:
            continue
        by_id[external_id] = DriverListItemResponse(
            driver_id=external_id,
            driver_name=resolve_driver_name(external_id, roster.display_name),
            tenant_id=tenant_id,
            is_live=external_id in active_ids,
            event_count=0,
            roster_active=roster.is_active,
            profile_complete=roster.profile_complete,
            has_unit_assignment=roster.has_unit_assignment,
            unit_label=roster.unit_label,
        )

    drivers = sorted(
        by_id.values(),
        key=lambda d: (
            0 if d.driver_name else 1,
            0 if d.event_count else 1,
            (d.driver_name or "").lower(),
            d.driver_id,
        ),
    )
    return drivers


async def _fetch_driver_events_for_day(
    session: AsyncSession,
    driver_id: str,
    day_end_utc: datetime,
    tenant_id: str,
) -> tuple[list[CanonicalHOSLogRecord], str | None]:
    """All events up to day end (needed for midnight carry-forward)."""
    stmt = (
        select(CanonicalHOSLogRecord)
        .where(
            CanonicalHOSLogRecord.tenant_id == tenant_id,
            CanonicalHOSLogRecord.driver_id == driver_id,
            CanonicalHOSLogRecord.event_timestamp < day_end_utc,
        )
        .order_by(CanonicalHOSLogRecord.event_timestamp.asc())
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())
    db_name: str | None = None
    for rec in records:
        if rec.driver_name:
            db_name = rec.driver_name
            break
    if db_name is None:
        name_stmt = (
            select(CanonicalHOSLogRecord.driver_name)
            .where(
                CanonicalHOSLogRecord.tenant_id == tenant_id,
                CanonicalHOSLogRecord.driver_id == driver_id,
                CanonicalHOSLogRecord.driver_name.is_not(None),
            )
            .limit(1)
        )
        name_result = await session.execute(name_stmt)
        db_name = name_result.scalar_one_or_none()
    return records, resolve_driver_name(driver_id, db_name)


async def _live_audit_markers(
    session: AsyncSession,
    driver_id: str,
    start_utc: datetime,
    end_utc: datetime,
    tenant_id: str,
) -> list[dict]:
    stmt = (
        select(AuditRecord)
        .where(
            AuditRecord.tenant_id == tenant_id,
            AuditRecord.driver_id == driver_id,
            AuditRecord.evaluated_at >= start_utc,
            AuditRecord.evaluated_at < end_utc,
        )
        .order_by(AuditRecord.evaluated_at.asc())
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())
    violations: list[dict] = []
    for rec in records:
        for v in rec.violations or []:
            if isinstance(v, dict):
                violations.append(v)
    return markers_from_audit_violations(
        violations, start_utc, end_utc, driver_id=driver_id
    )


async def _fleet_live_audit_markers(
    session: AsyncSession,
    start_utc: datetime | None,
    end_utc: datetime | None,
    tenant_id: str,
    driver_id: str | None = None,
) -> list[dict]:
    """Collect live audit violations across drivers for the Alerts tab."""
    stmt = select(AuditRecord).where(AuditRecord.tenant_id == tenant_id)
    if driver_id:
        stmt = stmt.where(AuditRecord.driver_id == driver_id)
    if start_utc is not None:
        stmt = stmt.where(AuditRecord.evaluated_at >= start_utc)
    if end_utc is not None:
        stmt = stmt.where(AuditRecord.evaluated_at < end_utc)
    stmt = stmt.order_by(AuditRecord.evaluated_at.desc()).limit(2000)
    result = await session.execute(stmt)
    records = list(result.scalars().all())
    markers: list[dict] = []
    for rec in records:
        for v in rec.violations or []:
            if not isinstance(v, dict):
                continue
            markers.append(
                {
                    **v,
                    "driver_id": rec.driver_id,
                    "source": "live_audit",
                }
            )
    # Collapse per driver/type/severity for the window
    window_start = start_utc or datetime.min.replace(tzinfo=UTC)
    window_end = end_utc or datetime.max.replace(tzinfo=UTC)
    by_driver: dict[str, list[dict]] = {}
    for m in markers:
        by_driver.setdefault(str(m.get("driver_id", "")), []).append(m)
    collapsed: list[dict] = []
    for did, group in by_driver.items():
        collapsed.extend(
            markers_from_audit_violations(
                group, window_start, window_end, driver_id=did
            )
        )
    return collapsed


async def _build_driver_day(
    session: AsyncSession,
    driver_id: str,
    local_date: date,
    tenant_id: str,
    display_tz: str | None = None,
) -> DriverDayResponse:
    tz_name = display_tz or default_display_timezone()
    bounds = chicago_day_bounds(local_date, zoneinfo_for(tz_name))
    records, driver_name = await _fetch_driver_events_for_day(
        session, driver_id, bounds.end_utc, tenant_id
    )
    if not records:
        # Still allow empty day if driver exists in active set / name known
        active_ids = set(await IngestionRepository.get_active_driver_ids(tenant_id))
        if driver_id not in active_ids:
            # Check any history at all
            exists_stmt = (
                select(func.count())
                .select_from(CanonicalHOSLogRecord)
                .where(
                    CanonicalHOSLogRecord.tenant_id == tenant_id,
                    CanonicalHOSLogRecord.driver_id == driver_id,
                )
            )
            exists_result = await session.execute(exists_stmt)
            if int(exists_result.scalar_one()) == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No HOS events found for driver {driver_id}",
                )

    raw_events = [
        RawHOSEvent(
            status=rec.status,
            event_timestamp=rec.event_timestamp,
            raw_id=rec.raw_id,
            device_id=rec.device_id,
            annotation=rec.annotation,
            latitude=rec.latitude,
            longitude=rec.longitude,
            odometer_m=rec.odometer_km,
            raw_payload=rec.raw_payload if isinstance(rec.raw_payload, dict) else None,
        )
        for rec in records
    ]
    grid_events, totals_seconds, carry = build_day_points(raw_events, bounds)

    unk_secs = totals_seconds.get("UNKNOWN", 0.0)
    total_tracked = sum(
        totals_seconds[s] for s in ("OFF", "SB", "D", "ON", "UNKNOWN")
    )
    pc_secs = totals_seconds.get("exemption_pc_seconds", 0.0)
    ym_secs = totals_seconds.get("exemption_ym_seconds", 0.0)
    dist_m = totals_seconds.get("distance_m", 0.0)
    totals = DurationTotalsResponse(
        OFF=format_duration_hhmm(totals_seconds["OFF"]),
        SB=format_duration_hhmm(totals_seconds["SB"]),
        D=format_duration_hhmm(totals_seconds["D"]),
        ON=format_duration_hhmm(totals_seconds["ON"]),
        UNKNOWN=format_duration_hhmm(unk_secs),
        OFF_seconds=totals_seconds["OFF"],
        SB_seconds=totals_seconds["SB"],
        D_seconds=totals_seconds["D"],
        ON_seconds=totals_seconds["ON"],
        UNKNOWN_seconds=unk_secs,
        exemption_pc_seconds=pc_secs,
        exemption_ym_seconds=ym_secs,
        exemption_pc=format_duration_hhmm(pc_secs),
        exemption_ym=format_duration_hhmm(ym_secs),
        total_hhmm=format_duration_hhmm(total_tracked),
        covers_full_day=abs(total_tracked - 86400) < 1.0,
        distance_m=dist_m,
        distance_mi=round(dist_m / METERS_PER_MILE, 2),
        distance_km=round(dist_m / 1000.0, 2),
        distance_label=format_distance_mi(dist_m) if dist_m > 0 else "",
        D_mi=round(totals_seconds.get("D_m", 0.0) / METERS_PER_MILE, 2),
        ON_mi=round(totals_seconds.get("ON_m", 0.0) / METERS_PER_MILE, 2),
        OFF_mi=round(totals_seconds.get("OFF_m", 0.0) / METERS_PER_MILE, 2),
        SB_mi=round(totals_seconds.get("SB_m", 0.0) / METERS_PER_MILE, 2),
        exemption_pc_mi=round(totals_seconds.get("exemption_pc_m", 0.0) / METERS_PER_MILE, 2),
        exemption_ym_mi=round(totals_seconds.get("exemption_ym_m", 0.0) / METERS_PER_MILE, 2),
    )

    backtest_rows = await load_backtest_dispatches() if _is_geotab_tenant(tenant_id) else []
    backtest = filter_backtest_markers(
        backtest_rows,
        driver_id,
        bounds.start_utc,
        bounds.end_utc,
    )
    live = await _live_audit_markers(
        session, driver_id, bounds.start_utc, bounds.end_utc, tenant_id
    )
    markers = annotate_marker_hours(
        merge_alert_markers(backtest, live),
        bounds.timezone,
    )
    attach_alerts_to_segments(grid_events, markers)

    active_ids = set(await IngestionRepository.get_active_driver_ids(tenant_id))

    return DriverDayResponse(
        driver_id=driver_id,
        driver_name=driver_name,
        tenant_id=tenant_id,
        date=local_date,
        timezone=bounds.timezone,
        day_start_utc=bounds.start_utc,
        day_end_utc=bounds.end_utc,
        is_live=driver_id in active_ids,
        carry_forward_status=carry,
        events=[DayStatusEventResponse(**e) for e in grid_events],
        totals=totals,
        alert_markers=[AlertMarkerResponse(**m) for m in markers],
    )


# ── Health ────────────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
async def health_check(
    session: AsyncSession = Depends(get_session),
) -> HealthResponse:
    """Extended health check verifying DB and Redis connectivity."""
    db_status = "unknown"
    redis_status = "unknown"

    try:
        await session.execute(select(func.now()))
        db_status = "healthy"
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        db_status = "unhealthy"

    try:
        redis = await get_redis()
        await redis.ping()
        redis_status = "healthy"
    except Exception as exc:
        logger.error("Redis health check failed: %s", exc)
        redis_status = "unhealthy"

    return HealthResponse(
        status="healthy" if db_status == "healthy" and redis_status == "healthy" else "degraded",
        environment=settings.ENVIRONMENT,
        database=db_status,
        redis=redis_status,
        rule_pack_version=settings.DEFAULT_RULE_PACK_VERSION,
    )


# ── All Drivers ───────────────────────────────────────────────────────────


@router.get("/drivers", response_model=DriverListResponse)
async def list_drivers(
    request: Request,
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    q: str | None = Query(default=None, description="Search name or driver id"),
    status_filter: str | None = Query(
        default=None, alias="status", description="Duty status filter"
    ),
    mode: str | None = Query(default=None, description="live | historical"),
    assignment: str | None = Query(
        default="assigned",
        description="assigned | unassigned | all (default assigned)",
    ),
    profile: str | None = Query(
        default="complete",
        description="complete | incomplete | all (default complete)",
    ),
    on_unit: bool | None = Query(
        default=None,
        description="When true, only drivers with a current unit assignment",
    ),
    session: AsyncSession = Depends(get_session),
) -> DriverListResponse:
    """Return drivers with historical HOS and/or live presence, roster-filtered."""
    active = await resolve_fleet(request, fleet_param=fleet)
    all_drivers = await _list_all_drivers(session, active.fleet_id)
    drivers = filter_drivers(
        all_drivers,
        q=q,
        status=status_filter,
        mode=mode,
        assignment=assignment,
        profile=profile,
        on_unit=on_unit,
    )
    return DriverListResponse(
        tenant_id=active.fleet_id,
        timezone=settings.DEFAULT_HOME_TERMINAL_TIMEZONE,
        total=len(drivers),
        drivers=drivers,
    )


# ── Active Drivers ────────────────────────────────────────────────────────


def _alert_stats_by_driver(
    alerts: list[dict[str, Any]],
) -> dict[str, tuple[int, int, str | None, str | None]]:
    """Group 30d fleet alerts into (warn, viol, latest_severity, latest_type) per driver."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"WARNING": 0, "VIOLATION": 0})
    latest: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        did = str(alert.get("driver_id", ""))
        if not did:
            continue
        sev = str(alert.get("severity", "")).upper()
        if sev in ("WARNING", "VIOLATION"):
            counts[did][sev] += 1
        as_of = alert.get("as_of")
        prev = latest.get(did)
        if prev is None or (isinstance(as_of, datetime) and as_of > prev["as_of"]):
            latest[did] = {
                "as_of": as_of if isinstance(as_of, datetime) else datetime.min.replace(tzinfo=UTC),
                "severity": str(alert.get("severity", "")) or None,
                "violation_type": str(alert.get("violation_type", "")) or None,
            }
    out: dict[str, tuple[int, int, str | None, str | None]] = {}
    for did, c in counts.items():
        lat = latest.get(did, {})
        out[did] = (
            c["WARNING"],
            c["VIOLATION"],
            lat.get("severity"),
            lat.get("violation_type"),
        )
    # Drivers with only non-W/V severities still need latest_* fields
    for did, lat in latest.items():
        if did not in out:
            out[did] = (0, 0, lat.get("severity"), lat.get("violation_type"))
    return out


@router.get("/drivers/positions", response_model=DriverPositionsResponse)
async def get_driver_positions(
    request: Request,
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> DriverPositionsResponse:
    """Latest non-null lat/lon per driver from canonical HOS logs.

    Includes last-30-day warning/violation counts and latest alert fields
    (same window as Home summary / fleet alerts).
    """
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id
    active_ids = set(await IngestionRepository.get_active_driver_ids(tenant_id))

    # PostgreSQL DISTINCT ON: newest event with coordinates per driver
    stmt = (
        select(CanonicalHOSLogRecord)
        .where(
            CanonicalHOSLogRecord.tenant_id == tenant_id,
            CanonicalHOSLogRecord.latitude.is_not(None),
            CanonicalHOSLogRecord.longitude.is_not(None),
        )
        .distinct(CanonicalHOSLogRecord.driver_id)
        .order_by(
            CanonicalHOSLogRecord.driver_id,
            CanonicalHOSLogRecord.event_timestamp.desc(),
        )
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())
    roster_by_id = await RosterRepository(session).map_by_external_id(tenant_id)

    start, end = default_alerts_utc_window(default_display_timezone())
    live = await _fleet_live_audit_markers(
        session, start, end, tenant_id, driver_id=None
    )
    backtest = await load_backtest_dispatches() if _is_geotab_tenant(tenant_id) else []
    merged = collect_fleet_alerts(
        backtest,
        live,
        start_utc=start,
        end_utc=end,
    )
    by_driver = _alert_stats_by_driver(merged)

    positions: list[DriverPositionResponse] = []
    for rec in records:
        if rec.latitude is None or rec.longitude is None:
            continue
        has_unit, unit_label = _position_roster_fields(rec.driver_id, roster_by_id)
        warn, viol, sev, vtype = by_driver.get(rec.driver_id, (0, 0, None, None))
        positions.append(
            DriverPositionResponse(
                driver_id=rec.driver_id,
                driver_name=resolve_driver_name(rec.driver_id, rec.driver_name),
                status=rec.status,
                latitude=float(rec.latitude),
                longitude=float(rec.longitude),
                event_timestamp=rec.event_timestamp,
                is_live=rec.driver_id in active_ids,
                warning_count=warn,
                violation_count=viol,
                latest_alert_severity=sev,
                latest_alert_type=vtype,
                has_unit_assignment=has_unit,
                unit_label=unit_label,
            )
        )
    positions.sort(
        key=lambda p: ((p.driver_name or "").lower(), p.driver_id),
    )
    return DriverPositionsResponse(
        tenant_id=tenant_id,
        total=len(positions),
        positions=positions,
    )


@router.get("/ingestion/recent", response_model=RecentIngestionResponse)
async def get_recent_ingestion(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> RecentIngestionResponse:
    """Newest canonical HOS logs by ingested_at for the active fleet."""
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id
    stmt = (
        select(CanonicalHOSLogRecord)
        .where(CanonicalHOSLogRecord.tenant_id == tenant_id)
        .order_by(
            CanonicalHOSLogRecord.ingested_at.desc().nulls_last(),
            CanonicalHOSLogRecord.event_timestamp.desc(),
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())
    events = [
        RecentIngestionItemResponse(
            ingested_at=rec.ingested_at,
            event_timestamp=rec.event_timestamp,
            driver_id=rec.driver_id,
            driver_name=resolve_driver_name(rec.driver_id, rec.driver_name),
            status=rec.status,
            device_id=rec.device_id,
            raw_id=rec.raw_id,
            latitude=float(rec.latitude) if rec.latitude is not None else None,
            longitude=float(rec.longitude) if rec.longitude is not None else None,
        )
        for rec in records
    ]
    return RecentIngestionResponse(
        tenant_id=tenant_id,
        total=len(events),
        events=events,
    )


@router.get("/drivers/active", response_model=list[DriverStatusResponse])
async def get_active_drivers(
    request: Request,
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> list[DriverStatusResponse]:
    """Return live status for all currently active drivers.

    Reads driver IDs from Redis set, then fetches latest event and audit
    record from PostgreSQL for each driver.
    """
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id
    driver_ids = await IngestionRepository.get_active_driver_ids(tenant_id)

    if not driver_ids:
        return []

    responses: list[DriverStatusResponse] = []
    engine_repo = EngineRepository(session)

    for driver_id in driver_ids:
        try:
            # Fetch most recent log event
            stmt = (
                select(CanonicalHOSLogRecord)
                .where(
                    CanonicalHOSLogRecord.tenant_id == tenant_id,
                    CanonicalHOSLogRecord.driver_id == driver_id,
                )
                .order_by(CanonicalHOSLogRecord.event_timestamp.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            latest_log = result.scalar_one_or_none()

            # Fetch latest audit record
            audit = await engine_repo.get_latest_audit_record(tenant_id, driver_id)

            responses.append(
                DriverStatusResponse(
                    driver_id=driver_id,
                    driver_name=resolve_driver_name(
                        driver_id,
                        latest_log.driver_name if latest_log else None,
                    ),
                    tenant_id=tenant_id,
                    current_status=latest_log.status if latest_log else "UNKNOWN",
                    last_event_at=latest_log.event_timestamp if latest_log else None,
                    is_compliant=audit.is_compliant if audit else True,
                    driving_remaining_minutes=(
                        round(audit.driving_remaining_seconds / 60, 1) if audit else None
                    ),
                    duty_window_remaining_minutes=(
                        round(audit.duty_window_remaining_seconds / 60, 1) if audit else None
                    ),
                    break_required=audit.break_required if audit else False,
                    weekly_hours_used=audit.weekly_hours_used if audit else None,
                    active_violation_count=(
                        len(audit.violations) if audit and audit.violations else 0
                    ),
                )
            )
        except Exception as exc:
            logger.error("Error fetching status for driver %s: %s", driver_id, exc)

    return responses


# ── Driver Day (home-terminal grid) ───────────────────────────────────────


@router.get("/alerts", response_model=FleetAlertsResponse)
async def list_fleet_alerts(
    request: Request,
    severity: str | None = Query(
        default=None,
        description="WARNING | VIOLATION (empty / all = no filter)",
    ),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    driver_id: str | None = Query(default=None),
    source: str | None = Query(
        default=None, description="backtest | live_audit (empty / all = both)"
    ),
    tz: str | None = Query(default=None),
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> FleetAlertsResponse:
    """Fleet alerts: merged backtest dispatches + recent live audit violations.

    Empty ``source`` / ``severity`` (and literal ``all``) mean no filter.
    When both ``from`` and ``to`` are omitted, defaults to the last 30 local days.
    Backtest markers are included only for the Geotab fleet.
    """
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id
    display_tz = tz or default_display_timezone()
    severity = normalize_filter_str(severity)
    source = normalize_filter_str(source)
    driver_id = normalize_filter_str(driver_id)

    start = from_ts
    end = to_ts
    if start is not None:
        start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    if end is not None:
        end = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
    if start is None and end is None:
        start, end = default_alerts_utc_window(display_tz)

    live: list[dict] = []
    if source is None or source.lower() == "live_audit":
        live = await _fleet_live_audit_markers(
            session, start, end, tenant_id, driver_id=driver_id
        )

    want_backtest = source is None or source.lower() == "backtest"
    backtest = (
        await load_backtest_dispatches()
        if want_backtest and _is_geotab_tenant(tenant_id)
        else []
    )
    merged = collect_fleet_alerts(
        backtest,
        live,
        severity=severity,
        driver_id=driver_id,
        source=source,
        start_utc=start,
        end_utc=end,
    )
    annotated = annotate_marker_hours(merged, display_tz)
    zone = zoneinfo_for(display_tz)
    items: list[FleetAlertItemResponse] = []
    for m in annotated:
        as_of = m["as_of"]
        if isinstance(as_of, datetime):
            day_date = as_of.astimezone(zone).date().isoformat()
        else:
            day_date = ""
        items.append(
            FleetAlertItemResponse(
                as_of=m["as_of"],
                local_timestamp=str(m.get("local_timestamp", "")),
                hour_of_day=float(m.get("hour_of_day", 0.0)),
                driver_id=str(m.get("driver_id", "")),
                driver_name=resolve_driver_name(
                    str(m.get("driver_id", "")), m.get("driver_name")
                ),
                violation_type=str(m.get("violation_type", "")),
                severity=str(m.get("severity", "")),
                rule_ref=str(m.get("rule_ref", "")),
                description=str(m.get("description", "")),
                source=str(m.get("source", "")),
                day_date=day_date,
            )
        )
    return FleetAlertsResponse(total=len(items), timezone=display_tz, alerts=items)


def _dispatch_channel(record: dict) -> str:
    """Infer notification channel from a JSONL dispatch record."""
    action = str(record.get("dispatch_action", "")).lower()
    if record.get("voice_call_sid") or "voice" in action:
        if record.get("sms_sid") or "sms" in action:
            return "voice+sms"
        return "voice"
    if record.get("sms_sid") or "sms" in action:
        return "sms"
    if "dry_run" in action or action.startswith("skipped_dry_run"):
        return "dry-run"
    if action == "suppressed" or record.get("suppressed"):
        return "suppressed"
    return action or "unknown"


@router.get("/alerts/dispatch-log", response_model=DispatchLogResponse)
async def get_alerts_dispatch_log(
    limit: int = Query(default=50, ge=1, le=500),
) -> DispatchLogResponse:
    """Latest Twilio / dry-run dispatch events from the JSONL alert log.

    Read-only — does not place any Twilio calls.
    """
    log_path = Path(settings.ALERT_LOG_PATH)
    raw = read_alert_log(limit=limit)
    events = [
        DispatchLogItemResponse(
            timestamp=str(row.get("timestamp") or "") or None,
            driver_id=str(row.get("driver_id", "")),
            driver_name=resolve_driver_name(str(row.get("driver_id", ""))),
            severity=str(row.get("severity", "")),
            violation_type=str(row.get("violation_type", "")),
            channel=_dispatch_channel(row),
            dispatch_action=str(row.get("dispatch_action", "")),
            suppressed=bool(row.get("suppressed", False)),
            description=str(row.get("description", "")),
            voice_call_sid=row.get("voice_call_sid"),
            sms_sid=row.get("sms_sid"),
        )
        for row in raw
    ]
    return DispatchLogResponse(
        total=len(events),
        path=str(log_path),
        events=events,
    )


@router.get("/ops/log", response_model=OpsLogResponse)
async def get_ops_log(
    limit: int = Query(default=50, ge=1, le=500),
) -> OpsLogResponse:
    """Latest dcw.* operational events from the JSONL ops log.

    Read-only file tail — useful for curl review alongside the Logs UI.
    """
    log_path = Path(settings.OPS_LOG_PATH)
    raw = read_ops_log(limit=limit)
    events = [
        OpsLogItemResponse(
            timestamp=str(row.get("timestamp") or "") or None,
            level=str(row.get("level") or "INFO"),
            logger=str(row.get("logger") or ""),
            message=str(row.get("message") or ""),
            process=str(row.get("process") or ""),
        )
        for row in raw
    ]
    return OpsLogResponse(
        total=len(events),
        path=str(log_path),
        events=events,
    )


@router.get("/drivers/{driver_id}/day", response_model=DriverDayResponse)
async def get_driver_day(
    request: Request,
    driver_id: str,
    date_str: str | None = Query(
        default=None,
        alias="date",
        description="Local calendar date YYYY-MM-DD (display TZ)",
    ),
    tz: str | None = Query(
        default=None,
        description="Display IANA timezone (default America/Chicago)",
    ),
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> DriverDayResponse:
    """Return HOS status events, duration totals, and alert markers for one day."""
    active = await resolve_fleet(request, fleet_param=fleet)
    display_tz = tz if tz else default_display_timezone()
    if date_str:
        try:
            local_date = date.fromisoformat(date_str)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="date must be YYYY-MM-DD",
            ) from exc
    else:
        local_date = datetime.now(zoneinfo_for(display_tz)).date()

    return await _build_driver_day(
        session, driver_id, local_date, active.fleet_id, display_tz=display_tz
    )


@router.get("/units/{device_id}/day/route", response_model=DriverDayRouteResponse)
async def get_unit_day_route(
    request: Request,
    device_id: str,
    date_str: str | None = Query(
        default=None,
        alias="date",
        description="Local calendar date YYYY-MM-DD (display TZ)",
    ),
    tz: str | None = Query(
        default=None,
        description="Display IANA timezone (default America/Chicago)",
    ),
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> DriverDayRouteResponse:
    """Return status-colored GPS route segments for one unit / local day."""
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id
    display_tz = tz if tz else default_display_timezone()
    if date_str:
        try:
            local_date = date.fromisoformat(date_str)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="date must be YYYY-MM-DD",
            ) from exc
    else:
        local_date = datetime.now(zoneinfo_for(display_tz)).date()

    bounds = chicago_day_bounds(local_date, zoneinfo_for(display_tz))
    repo = IngestionRepository(session)
    crumbs = await repo.get_gps_breadcrumbs_for_device_day(
        tenant_id=tenant_id,
        device_id=device_id,
        start_utc=bounds.start_utc,
        end_utc=bounds.end_utc,
    )
    hos_logs = await repo.get_hos_logs_for_device_day(
        tenant_id=tenant_id,
        device_id=device_id,
        start_utc=bounds.start_utc,
        end_utc=bounds.end_utc,
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
            "event_timestamp": log.event_timestamp,
            "status": log.status,
            "latitude": log.latitude,
            "longitude": log.longitude,
        }
        for log in hos_logs
    ]
    payload = build_day_route_payload(
        driver_id="",
        device_id=device_id,
        local_date=local_date,
        breadcrumbs=breadcrumb_dicts,
        hos_events=hos_events,
        alert_markers=[],
    )
    return DriverDayRouteResponse.model_validate(payload)


@router.get("/drivers/{driver_id}/day/route", response_model=DriverDayRouteResponse)
async def get_driver_day_route(
    request: Request,
    driver_id: str,
    date_str: str | None = Query(
        default=None,
        alias="date",
        description="Local calendar date YYYY-MM-DD (display TZ)",
    ),
    tz: str | None = Query(
        default=None,
        description="Display IANA timezone (default America/Chicago)",
    ),
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> DriverDayRouteResponse:
    """Return status-colored GPS route segments + alert points for one day."""
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id
    display_tz = tz if tz else default_display_timezone()
    if date_str:
        try:
            local_date = date.fromisoformat(date_str)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="date must be YYYY-MM-DD",
            ) from exc
    else:
        local_date = datetime.now(zoneinfo_for(display_tz)).date()

    day = await _build_driver_day(
        session, driver_id, local_date, tenant_id, display_tz=display_tz
    )
    repo = IngestionRepository(session)
    crumbs = await repo.get_gps_breadcrumbs_for_driver_day_route(
        tenant_id=tenant_id,
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
    # Prefer raw timeline statuses for ZOH (include carry-in via day builder events)
    alert_dicts = [m.model_dump() for m in day.alert_markers]
    payload = build_day_route_payload(
        driver_id=driver_id,
        local_date=local_date,
        breadcrumbs=breadcrumb_dicts,
        hos_events=hos_events,
        alert_markers=alert_dicts,
        carry_forward_status=day.carry_forward_status,
    )
    return DriverDayRouteResponse.model_validate(payload)


@router.get("/drivers/{driver_id}/alerts/detail", response_model=AlertDetailResponse)
async def get_alert_detail(
    request: Request,
    driver_id: str,
    as_of: datetime = Query(..., description="Alert timestamp (UTC or offset)"),
    violation_type: str = Query(..., description="Violation type enum value"),
    source: str = Query(default="backtest", description="backtest | live_audit"),
    tz: str | None = Query(default=None, description="Display IANA timezone"),
    description: str = Query(default="", description="Original marker description"),
    severity: str = Query(default="", description="Original marker severity"),
    rule_ref: str = Query(default="", description="Original marker rule ref"),
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> AlertDetailResponse:
    """Recompute compliance at ``as_of`` and return calculation detail + graph context."""
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id
    display_tz = tz or default_display_timezone()
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    else:
        as_of = as_of.astimezone(UTC)

    lookback = settings.WEEKLY_CYCLE_DAYS + 3
    cutoff = as_of - timedelta(days=lookback)
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No HOS events for driver {driver_id} near {as_of.isoformat()}",
        )

    db_name = next((r.driver_name for r in records if r.driver_name), None)
    driver_name = resolve_driver_name(driver_id, db_name)
    events = logs_to_events(records)
    profile = await EngineRepository(session).get_driver_profile(tenant_id, driver_id)
    payload = build_alert_detail(
        driver_id=driver_id,
        tenant_id=tenant_id,
        driver_name=driver_name,
        events=events,
        as_of=as_of,
        violation_type=violation_type,
        source=source,
        display_tz_name=display_tz,
        description_hint=description,
        severity_hint=severity,
        rule_ref_hint=rule_ref,
        profile=profile,
    )
    return AlertDetailResponse.model_validate(payload)


@router.get("/drivers/{driver_id}/alert-markers", response_model=AlertMarkersResponse)
async def get_driver_alert_markers(
    request: Request,
    driver_id: str,
    from_ts: datetime = Query(..., alias="from", description="UTC window start"),
    to_ts: datetime = Query(..., alias="to", description="UTC window end (exclusive)"),
    tz: str | None = Query(default=None, description="Display IANA timezone"),
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> AlertMarkersResponse:
    """Merge backtest would-dispatch markers with live audit violations."""
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id
    if from_ts.tzinfo is None:
        from_ts = from_ts.replace(tzinfo=UTC)
    else:
        from_ts = from_ts.astimezone(UTC)
    if to_ts.tzinfo is None:
        to_ts = to_ts.replace(tzinfo=UTC)
    else:
        to_ts = to_ts.astimezone(UTC)

    display_tz = tz or default_display_timezone()
    backtest_rows = await load_backtest_dispatches() if _is_geotab_tenant(tenant_id) else []
    backtest = filter_backtest_markers(
        backtest_rows,
        driver_id,
        from_ts,
        to_ts,
    )
    live = await _live_audit_markers(session, driver_id, from_ts, to_ts, tenant_id)
    markers = annotate_marker_hours(
        merge_alert_markers(backtest, live),
        display_tz,
    )
    return AlertMarkersResponse(
        driver_id=driver_id,
        from_ts=from_ts,
        to_ts=to_ts,
        markers=[AlertMarkerResponse(**m) for m in markers],
    )


# ── Driver Timeline ───────────────────────────────────────────────────────


@router.get("/drivers/{driver_id}/timeline", response_model=DriverTimelineResponse)
async def get_driver_timeline(
    request: Request,
    driver_id: str,
    limit: int = Query(default=200, le=1000, description="Max events to return"),
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> DriverTimelineResponse:
    """Return a driver's historical HOS event timeline from PostgreSQL."""
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id

    stmt = (
        select(CanonicalHOSLogRecord)
        .where(
            CanonicalHOSLogRecord.tenant_id == tenant_id,
            CanonicalHOSLogRecord.driver_id == driver_id,
        )
        .order_by(CanonicalHOSLogRecord.event_timestamp.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())

    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No HOS events found for driver {driver_id}",
        )

    events = [
        HOSEventResponse(
            raw_id=rec.raw_id,
            status=rec.status,
            event_timestamp=rec.event_timestamp,
            device_id=rec.device_id,
            latitude=rec.latitude,
            longitude=rec.longitude,
            odometer_km=rec.odometer_km,
            annotation=rec.annotation,
            inputs_hash=rec.inputs_hash,
        )
        for rec in records
    ]

    return DriverTimelineResponse(
        driver_id=driver_id,
        tenant_id=tenant_id,
        total_events=len(events),
        events=events,
    )


# ── Compliance Snapshot ───────────────────────────────────────────────────


@router.get("/drivers/{driver_id}/compliance", response_model=ComplianceSnapshotResponse)
async def get_driver_compliance(
    request: Request,
    driver_id: str,
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> ComplianceSnapshotResponse:
    """Return the latest compliance evaluation result for a driver."""
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id
    engine_repo = EngineRepository(session)

    audit = await engine_repo.get_latest_audit_record(tenant_id, driver_id)
    if not audit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No compliance audit found for driver {driver_id}",
        )

    violations = [
        ViolationResponse(**v) for v in (audit.violations or [])
    ]

    return ComplianceSnapshotResponse(
        driver_id=driver_id,
        tenant_id=tenant_id,
        evaluated_at=audit.evaluated_at,
        rule_pack_version=audit.rule_pack_version,
        is_compliant=audit.is_compliant,
        driving_remaining_seconds=audit.driving_remaining_seconds,
        duty_window_remaining_seconds=audit.duty_window_remaining_seconds,
        break_required=audit.break_required,
        weekly_hours_used=audit.weekly_hours_used,
        weekly_hours_remaining=audit.weekly_hours_remaining,
        violations=violations,
    )


# ── Audit Records ─────────────────────────────────────────────────────────


@router.get("/audit/records", response_model=PaginatedAuditResponse)
async def list_audit_records(
    request: Request,
    driver_id: str = Query(None, description="Filter by driver ID"),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    fleet: str | None = Query(default=None, description="Fleet id (tenant scope)"),
    session: AsyncSession = Depends(get_session),
) -> PaginatedAuditResponse:
    """Return a paginated list of compliance audit records."""
    active = await resolve_fleet(request, fleet_param=fleet)
    tenant_id = active.fleet_id

    base_query = select(AuditRecord).where(AuditRecord.tenant_id == tenant_id)
    count_query = select(func.count()).select_from(AuditRecord).where(
        AuditRecord.tenant_id == tenant_id
    )

    if driver_id:
        base_query = base_query.where(AuditRecord.driver_id == driver_id)
        count_query = count_query.where(AuditRecord.driver_id == driver_id)

    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    stmt = (
        base_query
        .order_by(AuditRecord.evaluated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())

    return PaginatedAuditResponse(
        total=total,
        limit=limit,
        offset=offset,
        records=[
            AuditRecordResponse(
                id=str(rec.id),
                tenant_id=rec.tenant_id,
                driver_id=rec.driver_id,
                evaluated_at=rec.evaluated_at,
                rule_pack_version=rec.rule_pack_version,
                is_compliant=rec.is_compliant,
                weekly_hours_used=rec.weekly_hours_used,
                driving_remaining_seconds=rec.driving_remaining_seconds,
                violation_count=len(rec.violations) if rec.violations else 0,
            )
            for rec in records
        ],
    )
