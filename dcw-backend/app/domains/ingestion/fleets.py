"""Fleet registry — one fleet per telematics API connection.

Each fleet's ``fleet_id`` is the ``tenant_id`` partition key used across
``canonical_hos_logs``, ``gps_breadcrumbs``, ``audit_records``, and Redis.
Fleets are bootstrapped from environment credentials on startup and stored
in the mutable ``fleets`` table (no append-only trigger).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.database import async_session_factory
from app.domains.ingestion.models import FleetRecord

logger = logging.getLogger("dcw.ingestion.fleets")


class Fleet(BaseModel):
    """Immutable view of one registered telematics fleet."""

    model_config = ConfigDict(frozen=True)

    fleet_id: str
    provider: str
    display_name: str
    enabled: bool = True


def env_configured_fleets() -> list[Fleet]:
    """Build the fleet list implied by configured environment credentials."""
    fleets: list[Fleet] = []
    if settings.GEOTAB_DATABASE and settings.GEOTAB_USERNAME and settings.GEOTAB_PASSWORD:
        fleets.append(
            Fleet(
                fleet_id=settings.GEOTAB_DATABASE,
                provider="geotab",
                display_name=settings.GEOTAB_DATABASE,
                enabled=True,
            )
        )
    if settings.SAMSARA_API_TOKEN:
        fleets.append(
            Fleet(
                fleet_id=settings.SAMSARA_FLEET_ID or "samsara:default",
                provider="samsara",
                display_name="Samsara",
                enabled=True,
            )
        )
    return fleets


async def sync_fleets_to_db() -> list[Fleet]:
    """Upsert env-configured fleets into the ``fleets`` table.

    Existing rows keep their operator-controlled ``enabled`` flag; provider
    and display name are refreshed from the environment.
    """
    fleets = env_configured_fleets()
    if not fleets:
        logger.info("No telematics credentials configured — fleet registry empty")
        return []

    async with async_session_factory() as session:
        for fleet in fleets:
            stmt = (
                pg_insert(FleetRecord)
                .values(
                    fleet_id=fleet.fleet_id,
                    provider=fleet.provider,
                    display_name=fleet.display_name,
                    enabled=fleet.enabled,
                )
                .on_conflict_do_update(
                    index_elements=["fleet_id"],
                    set_={
                        "provider": fleet.provider,
                        "display_name": fleet.display_name,
                    },
                )
            )
            await session.execute(stmt)
        await session.commit()

    logger.info(
        "Fleet registry synced: %s",
        ", ".join(f"{f.provider}:{f.fleet_id}" for f in fleets),
    )
    return fleets


def _record_to_fleet(record: FleetRecord) -> Fleet:
    return Fleet(
        fleet_id=str(record.fleet_id),
        provider=str(record.provider),
        display_name=str(record.display_name),
        enabled=bool(record.enabled),
    )


async def list_enabled_fleets() -> list[Fleet]:
    """Return enabled fleets from the DB registry, falling back to env config."""
    try:
        async with async_session_factory() as session:
            stmt = (
                select(FleetRecord)
                .where(FleetRecord.enabled.is_(True))
                .order_by(FleetRecord.provider.asc(), FleetRecord.fleet_id.asc())
            )
            result = await session.execute(stmt)
            records = list(result.scalars().all())
    except Exception:
        logger.warning("Fleet registry query failed — using env fallback", exc_info=True)
        return env_configured_fleets()

    if not records:
        return env_configured_fleets()
    return [_record_to_fleet(record) for record in records]


async def get_fleet(fleet_id: str) -> Fleet | None:
    """Look up one fleet by id (DB first, env fallback)."""
    try:
        async with async_session_factory() as session:
            record = await session.get(FleetRecord, fleet_id)
    except Exception:
        logger.warning("Fleet lookup failed for %s — using env fallback", fleet_id, exc_info=True)
        record = None

    if record is not None:
        return _record_to_fleet(record)
    for fleet in env_configured_fleets():
        if fleet.fleet_id == fleet_id:
            return fleet
    return None
