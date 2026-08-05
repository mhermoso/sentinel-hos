"""Engine repository — fetches driver timelines and persists audit records."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.engine.models import AuditRecord, DriverProfileRecord
from app.domains.engine.replay import (
    WEEKLY_DUTY_LOOKBACK_BUFFER_DAYS,
    compute_weekly_duty_seconds,
)
from app.domains.engine.schemas import (
    ComplianceResult,
    DayAnnotations,
    DriverProfile,
    DriverTimeline,
    HosCycle,
    OperatingAuthority,
    WorkReportingLocation,
    default_driver_profile,
)
from app.domains.engine.short_haul import home_terminal_day
from app.domains.ingestion.duty_filter import should_skip_duty_status_change
from app.domains.ingestion.models import CanonicalHOSLogRecord
from app.domains.ingestion.schemas import CanonicalDutyStatus

logger = logging.getLogger("dcw.engine.repository")


def _record_to_profile(record: DriverProfileRecord) -> DriverProfile:
    location = None
    if record.work_reporting_lat is not None and record.work_reporting_lon is not None:
        location = WorkReportingLocation(
            latitude=record.work_reporting_lat,
            longitude=record.work_reporting_lon,
        )
    return DriverProfile(
        driver_id=record.driver_id,
        tenant_id=record.tenant_id,
        operating_authority=OperatingAuthority(record.operating_authority),
        short_haul_eligible=bool(record.short_haul_eligible),
        cdl_required=bool(record.cdl_required),
        cycle=HosCycle.parse(str(record.cycle)),
        home_terminal_timezone=record.home_terminal_timezone,
        work_reporting_location=location,
        vehicle_weight_class=record.vehicle_weight_class,
        hazmat_placard=record.hazmat_placard,
    )


class EngineRepository:
    """Data-access layer for the compliance engine domain."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_driver_profile(
        self,
        tenant_id: str,
        driver_id: str,
    ) -> DriverProfile:
        """Load a driver profile, or interstate 70/8 defaults when missing."""
        stmt = select(DriverProfileRecord).where(
            DriverProfileRecord.tenant_id == tenant_id,
            DriverProfileRecord.driver_id == driver_id,
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return default_driver_profile(driver_id=driver_id, tenant_id=tenant_id)
        return _record_to_profile(record)

    async def get_day_annotations(
        self,
        tenant_id: str,
        driver_id: str,
        *,
        as_of: datetime,
        home_terminal_timezone: str,
    ) -> DayAnnotations:
        """Load per home-terminal-day exception flags and form & manner evidence.

        Persistence stub for Phase 6: returns empty annotations until a
        day-annotation store (JSON map on profile or dedicated table) is wired.
        Callers/tests may pass ``DayAnnotations`` directly into
        ``RulePack.evaluate``.
        """
        day = home_terminal_day(as_of, home_terminal_timezone)
        logger.debug(
            "Day annotations stub tenant=%s driver=%s day=%s → empty",
            tenant_id,
            driver_id,
            day,
        )
        return DayAnnotations()

    async def upsert_driver_profile(self, profile: DriverProfile) -> DriverProfile:
        """Insert or update a driver profile row and return the stored profile."""
        lat = (
            profile.work_reporting_location.latitude
            if profile.work_reporting_location
            else None
        )
        lon = (
            profile.work_reporting_location.longitude
            if profile.work_reporting_location
            else None
        )
        now = datetime.now(UTC)
        stmt = (
            pg_insert(DriverProfileRecord)
            .values(
                tenant_id=profile.tenant_id,
                driver_id=profile.driver_id,
                operating_authority=profile.operating_authority.value,
                short_haul_eligible=profile.short_haul_eligible,
                cdl_required=profile.cdl_required,
                cycle=profile.cycle.value,
                home_terminal_timezone=profile.home_terminal_timezone,
                work_reporting_lat=lat,
                work_reporting_lon=lon,
                vehicle_weight_class=profile.vehicle_weight_class,
                hazmat_placard=profile.hazmat_placard,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_driver_profiles_tenant_driver",
                set_={
                    "operating_authority": profile.operating_authority.value,
                    "short_haul_eligible": profile.short_haul_eligible,
                    "cdl_required": profile.cdl_required,
                    "cycle": profile.cycle.value,
                    "home_terminal_timezone": profile.home_terminal_timezone,
                    "work_reporting_lat": lat,
                    "work_reporting_lon": lon,
                    "vehicle_weight_class": profile.vehicle_weight_class,
                    "hazmat_placard": profile.hazmat_placard,
                    "updated_at": now,
                },
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()
        return await self.get_driver_profile(profile.tenant_id, profile.driver_id)

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
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

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
            # raw_id tie-break keeps ordering (and audit hashes) deterministic
            # when multiple rows share one event_timestamp (e.g. edited logs).
            .order_by(
                CanonicalHOSLogRecord.event_timestamp.asc(),
                CanonicalHOSLogRecord.raw_id.asc(),
            )
        )

        result = await self.session.execute(stmt)
        records = list(result.scalars().all())

        events = [
            DriverTimeline.HOSEvent(
                status=rec.status,
                timestamp=rec.event_timestamp,
            )
            for rec in records
            if not should_skip_duty_status_change(
                rec.status,
                rec.raw_payload if isinstance(rec.raw_payload, dict) else None,
            )
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
        as_of: datetime | None = None,
    ) -> float:
        """Sum total on-duty seconds over the rolling weekly cycle window."""
        now = as_of if as_of is not None else datetime.now(UTC)
        lookback_days = cycle_days + WEEKLY_DUTY_LOOKBACK_BUFFER_DAYS
        cutoff = now - timedelta(days=lookback_days)

        stmt = (
            select(CanonicalHOSLogRecord)
            .where(
                CanonicalHOSLogRecord.tenant_id == tenant_id,
                CanonicalHOSLogRecord.driver_id == driver_id,
                CanonicalHOSLogRecord.event_timestamp >= cutoff,
                CanonicalHOSLogRecord.event_timestamp <= now,
                CanonicalHOSLogRecord.status.notin_(
                    [CanonicalDutyStatus.UNKNOWN.value]
                ),
            )
            .order_by(
                CanonicalHOSLogRecord.event_timestamp.asc(),
                CanonicalHOSLogRecord.raw_id.asc(),
            )
        )

        result = await self.session.execute(stmt)
        records = list(result.scalars().all())

        events = [
            DriverTimeline.HOSEvent(
                status=rec.status,
                timestamp=rec.event_timestamp,
            )
            for rec in records
            if not should_skip_duty_status_change(
                rec.status,
                rec.raw_payload if isinstance(rec.raw_payload, dict) else None,
            )
        ]
        return compute_weekly_duty_seconds(events, cycle_days=cycle_days, as_of=now)

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
    ) -> AuditRecord | None:
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

    async def get_latest_audit_records_batch(
        self,
        tenant_id: str,
        driver_ids: list[str],
    ) -> dict[str, AuditRecord]:
        """Fetch the most recent audit record per driver in one query."""
        if not driver_ids:
            return {}
        stmt = (
            select(AuditRecord)
            .distinct(AuditRecord.driver_id)
            .where(
                AuditRecord.tenant_id == tenant_id,
                AuditRecord.driver_id.in_(driver_ids),
            )
            .order_by(AuditRecord.driver_id, AuditRecord.evaluated_at.desc())
        )
        result = await self.session.execute(stmt)
        records = list(result.scalars().all())
        return {rec.driver_id: rec for rec in records}
