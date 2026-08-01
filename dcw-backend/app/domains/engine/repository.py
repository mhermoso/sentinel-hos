"""Engine repository — fetches driver timelines and persists audit records."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.engine.models import AuditRecord
from app.domains.engine.schemas import ComplianceResult, DriverTimeline
from app.domains.ingestion.models import CanonicalHOSLogRecord
from app.domains.ingestion.schemas import CanonicalDutyStatus

logger = logging.getLogger("dcw.engine.repository")


class EngineRepository:
    """Data-access layer for the compliance engine domain."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_driver_timeline(
        self,
        tenant_id: str,
        driver_id: str,
        lookback_days: int = 8,
    ) -> DriverTimeline:
        """Fetch a driver's HOS event timeline from PostgreSQL.

        Args:
            tenant_id: Customer identifier.
            driver_id: Driver identifier.
            lookback_days: How many days back to query (8 for 70h cycle).

        Returns:
            DriverTimeline with events ordered chronologically.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        stmt = (
            select(CanonicalHOSLogRecord)
            .where(
                CanonicalHOSLogRecord.tenant_id == tenant_id,
                CanonicalHOSLogRecord.driver_id == driver_id,
                CanonicalHOSLogRecord.event_timestamp >= cutoff,
                CanonicalHOSLogRecord.status.notin_(
                    [CanonicalDutyStatus.UNKNOWN.value]
                ),
            )
            .order_by(CanonicalHOSLogRecord.event_timestamp.asc())
        )

        result = await self.session.execute(stmt)
        records = list(result.scalars().all())

        events = [
            DriverTimeline.HOSEvent(
                status=rec.status,
                timestamp=rec.event_timestamp,
            )
            for rec in records
        ]

        return DriverTimeline(
            driver_id=driver_id,
            tenant_id=tenant_id,
            events=events,
        )

    async def get_weekly_duty_seconds(
        self,
        tenant_id: str,
        driver_id: str,
        cycle_days: int,
    ) -> float:
        """Sum total on-duty seconds over the rolling weekly cycle window."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=cycle_days)

        stmt = (
            select(CanonicalHOSLogRecord)
            .where(
                CanonicalHOSLogRecord.tenant_id == tenant_id,
                CanonicalHOSLogRecord.driver_id == driver_id,
                CanonicalHOSLogRecord.event_timestamp >= cutoff,
                CanonicalHOSLogRecord.status.in_(
                    [
                        CanonicalDutyStatus.ON_DUTY.value,
                        CanonicalDutyStatus.DRIVING.value,
                        CanonicalDutyStatus.YARD_MOVE.value,
                    ]
                ),
            )
            .order_by(CanonicalHOSLogRecord.event_timestamp.asc())
        )

        result = await self.session.execute(stmt)
        records = list(result.scalars().all())

        # Compute duration between consecutive events
        total_seconds = 0.0
        now = datetime.now(timezone.utc)
        for i, rec in enumerate(records):
            if i + 1 < len(records):
                delta = records[i + 1].event_timestamp - rec.event_timestamp
                total_seconds += max(0.0, delta.total_seconds())

        return total_seconds

    async def persist_audit_record(self, result: ComplianceResult) -> None:
        """Persist a compliance evaluation result as an immutable audit record."""
        stmt = pg_insert(AuditRecord).values(
            tenant_id=result.tenant_id,
            driver_id=result.driver_id,
            inputs_hash=result.inputs_hash,
            rule_pack_version=result.rule_pack_version,
            driving_remaining_seconds=result.driving_remaining_seconds,
            duty_window_remaining_seconds=result.duty_window_remaining_seconds,
            break_required=result.break_required,
            weekly_hours_used=result.weekly_hours_used,
            weekly_hours_remaining=result.weekly_hours_remaining,
            is_compliant=result.is_compliant,
            violations=[v.model_dump(mode="json") for v in result.violations],
            raw_output=result.model_dump(mode="json"),
            evaluated_at=result.evaluated_at,
        )
        await self.session.execute(stmt)
        await self.session.flush()
        logger.debug(
            "Persisted audit record driver=%s compliant=%s",
            result.driver_id,
            result.is_compliant,
        )

    async def get_latest_audit_record(
        self,
        tenant_id: str,
        driver_id: str,
    ) -> Optional[AuditRecord]:
        """Fetch the most recent audit record for a driver."""
        stmt = (
            select(AuditRecord)
            .where(
                AuditRecord.tenant_id == tenant_id,
                AuditRecord.driver_id == driver_id,
            )
            .order_by(AuditRecord.evaluated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
