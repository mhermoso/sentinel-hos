"""Persistence for the mutable ``driver_roster`` cache."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ingestion.models import DriverRosterRecord
from app.domains.ingestion.schemas import DriverRosterEntry

logger = logging.getLogger("dcw.ingestion.roster_repository")


def _record_to_entry(record: DriverRosterRecord) -> DriverRosterEntry:
    return DriverRosterEntry(
        provider=str(record.provider),
        tenant_id=str(record.tenant_id),
        external_driver_id=str(record.external_driver_id),
        first_name=record.first_name,
        last_name=record.last_name,
        display_name=record.display_name,
        phone_e164=record.phone_e164,
        current_device_id=record.current_device_id,
        unit_label=record.unit_label,
        is_active=bool(record.is_active),
        profile_complete=bool(record.profile_complete),
        has_unit_assignment=bool(record.has_unit_assignment),
    )


class RosterRepository:
    """Upsert / query driver roster rows for a tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_entries(self, entries: list[DriverRosterEntry]) -> int:
        """Insert or update roster rows; returns number of entries processed."""
        if not entries:
            return 0
        now = datetime.now(UTC)
        for entry in entries:
            stmt = (
                pg_insert(DriverRosterRecord)
                .values(
                    provider=entry.provider,
                    tenant_id=entry.tenant_id,
                    external_driver_id=entry.external_driver_id,
                    first_name=entry.first_name,
                    last_name=entry.last_name,
                    display_name=entry.display_name,
                    phone_e164=entry.phone_e164,
                    current_device_id=entry.current_device_id,
                    unit_label=entry.unit_label,
                    is_active=entry.is_active,
                    profile_complete=entry.profile_complete,
                    has_unit_assignment=entry.has_unit_assignment,
                    synced_at=now,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_driver_roster_provider_tenant_external",
                    set_={
                        "first_name": entry.first_name,
                        "last_name": entry.last_name,
                        "display_name": entry.display_name,
                        "phone_e164": entry.phone_e164,
                        "current_device_id": entry.current_device_id,
                        "unit_label": entry.unit_label,
                        "is_active": entry.is_active,
                        "profile_complete": entry.profile_complete,
                        "has_unit_assignment": entry.has_unit_assignment,
                        "synced_at": now,
                        "updated_at": now,
                    },
                )
            )
            await self.session.execute(stmt)
        await self.session.flush()
        logger.info(
            "Upserted %d driver_roster rows (provider=%s tenant=%s)",
            len(entries),
            entries[0].provider,
            entries[0].tenant_id,
        )
        return len(entries)

    async def list_for_tenant(self, tenant_id: str) -> list[DriverRosterEntry]:
        """Return all roster rows for a tenant (any provider)."""
        stmt = select(DriverRosterRecord).where(DriverRosterRecord.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return [_record_to_entry(r) for r in result.scalars().all()]

    async def map_by_external_id(self, tenant_id: str) -> dict[str, DriverRosterEntry]:
        """Map ``external_driver_id`` → roster entry for one tenant."""
        entries = await self.list_for_tenant(tenant_id)
        return {e.external_driver_id: e for e in entries}
