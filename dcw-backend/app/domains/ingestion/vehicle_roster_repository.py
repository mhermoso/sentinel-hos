"""Persistence for the mutable ``vehicle_roster`` cache."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ingestion.models import VehicleRosterRecord
from app.domains.ingestion.schemas import VehicleRosterEntry

logger = logging.getLogger("dcw.ingestion.vehicle_roster_repository")


def _record_to_entry(record: VehicleRosterRecord) -> VehicleRosterEntry:
    return VehicleRosterEntry(
        provider=str(record.provider),
        tenant_id=str(record.tenant_id),
        external_device_id=str(record.external_device_id),
        name=record.name,
        vin=record.vin,
        current_driver_id=record.current_driver_id,
    )


class VehicleRosterRepository:
    """Upsert / query vehicle roster rows for a tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_entries(self, entries: list[VehicleRosterEntry]) -> int:
        """Insert or update vehicle roster rows; returns number processed."""
        if not entries:
            return 0
        now = datetime.now(UTC)
        for entry in entries:
            stmt = (
                pg_insert(VehicleRosterRecord)
                .values(
                    provider=entry.provider,
                    tenant_id=entry.tenant_id,
                    external_device_id=entry.external_device_id,
                    name=entry.name,
                    vin=entry.vin,
                    current_driver_id=entry.current_driver_id,
                    synced_at=now,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_vehicle_roster_provider_tenant_external",
                    set_={
                        "name": entry.name,
                        "vin": entry.vin,
                        "current_driver_id": entry.current_driver_id,
                        "synced_at": now,
                        "updated_at": now,
                    },
                )
            )
            await self.session.execute(stmt)
        await self.session.flush()
        logger.info(
            "Upserted %d vehicle_roster rows (provider=%s tenant=%s)",
            len(entries),
            entries[0].provider,
            entries[0].tenant_id,
        )
        return len(entries)

    async def list_for_tenant(self, tenant_id: str) -> list[VehicleRosterEntry]:
        """Return all vehicle roster rows for a tenant (any provider)."""
        stmt = select(VehicleRosterRecord).where(VehicleRosterRecord.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return [_record_to_entry(r) for r in result.scalars().all()]

    async def map_by_external_id(self, tenant_id: str) -> dict[str, VehicleRosterEntry]:
        """Map ``external_device_id`` → vehicle roster entry for one tenant."""
        entries = await self.list_for_tenant(tenant_id)
        return {e.external_device_id: e for e in entries}

    async def get_by_external_id(
        self,
        tenant_id: str,
        external_device_id: str,
    ) -> VehicleRosterEntry | None:
        """Return one vehicle roster row or None."""
        stmt = (
            select(VehicleRosterRecord)
            .where(
                VehicleRosterRecord.tenant_id == tenant_id,
                VehicleRosterRecord.external_device_id == external_device_id,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        return _record_to_entry(record) if record is not None else None
