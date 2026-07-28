"""FastAPI router for the DCW dashboard API.

Provides REST endpoints for:
  - GET /api/health            — extended health check
  - GET /api/drivers/active    — live driver statuses from Redis + PG
  - GET /api/drivers/{id}/timeline   — historical HOS log query
  - GET /api/drivers/{id}/compliance — latest compliance result
  - GET /api/audit/records     — paginated audit record listing
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.redis import get_redis
from app.domains.dashboard.schemas import (
    AuditRecordResponse,
    ComplianceSnapshotResponse,
    DriverStatusResponse,
    DriverTimelineResponse,
    HOSEventResponse,
    HealthResponse,
    PaginatedAuditResponse,
    ViolationResponse,
)
from app.domains.engine.models import AuditRecord
from app.domains.engine.repository import EngineRepository
from app.domains.ingestion.models import CanonicalHOSLogRecord
from app.domains.ingestion.repository import IngestionRepository

logger = logging.getLogger("dcw.dashboard.router")

router = APIRouter(prefix="/api", tags=["dashboard"])


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


# ── Active Drivers ────────────────────────────────────────────────────────


@router.get("/drivers/active", response_model=List[DriverStatusResponse])
async def get_active_drivers(
    session: AsyncSession = Depends(get_session),
) -> List[DriverStatusResponse]:
    """Return live status for all currently active drivers.

    Reads driver IDs from Redis set, then fetches latest event and audit
    record from PostgreSQL for each driver.
    """
    tenant_id = settings.GEOTAB_DATABASE
    driver_ids = await IngestionRepository.get_active_driver_ids()

    if not driver_ids:
        return []

    responses: List[DriverStatusResponse] = []
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
                    driver_name=latest_log.driver_name if latest_log else None,
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


# ── Driver Timeline ───────────────────────────────────────────────────────


@router.get("/drivers/{driver_id}/timeline", response_model=DriverTimelineResponse)
async def get_driver_timeline(
    driver_id: str,
    limit: int = Query(default=200, le=1000, description="Max events to return"),
    session: AsyncSession = Depends(get_session),
) -> DriverTimelineResponse:
    """Return a driver's historical HOS event timeline from PostgreSQL."""
    tenant_id = settings.GEOTAB_DATABASE

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
    driver_id: str,
    session: AsyncSession = Depends(get_session),
) -> ComplianceSnapshotResponse:
    """Return the latest compliance evaluation result for a driver."""
    tenant_id = settings.GEOTAB_DATABASE
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
    driver_id: str = Query(None, description="Filter by driver ID"),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> PaginatedAuditResponse:
    """Return a paginated list of compliance audit records."""
    tenant_id = settings.GEOTAB_DATABASE

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
