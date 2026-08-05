"""ARQ/cron roster sync — upserts ``driver_roster`` from provider adapters.

Never filters or drops HOS events. Motive fleets are skipped until the
adapter implements roster fetch (same idle pattern as HOS poll).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.database import async_session_factory
from app.domains.ingestion.adapters import BaseTelematicsAdapter
from app.domains.ingestion.fleets import list_enabled_fleets
from app.domains.ingestion.roster_repository import RosterRepository

logger = logging.getLogger("dcw.ingestion.roster_sync")


async def sync_fleet_roster(
    adapter: BaseTelematicsAdapter,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Fetch driver roster for one fleet and upsert into Postgres."""
    try:
        entries = await adapter.fetch_driver_roster(tenant_id)
    except NotImplementedError:
        logger.info(
            "Roster sync skipped — adapter %s not implemented (tenant=%s)",
            adapter.provider_name,
            tenant_id,
        )
        return {
            "provider": adapter.provider_name,
            "tenant_id": tenant_id,
            "skipped": True,
            "reason": "not_implemented",
            "upserted": 0,
        }

    async with async_session_factory() as session:
        repo = RosterRepository(session)
        upserted = await repo.upsert_entries(entries)
        await session.commit()

    return {
        "provider": adapter.provider_name,
        "tenant_id": tenant_id,
        "skipped": False,
        "upserted": upserted,
        "active": sum(1 for e in entries if e.is_active),
        "complete": sum(1 for e in entries if e.profile_complete),
        "with_unit": sum(1 for e in entries if e.has_unit_assignment),
    }


async def sync_driver_rosters(ctx: dict[str, Any]) -> dict[str, Any]:
    """ARQ cron task — sync Geotab + Samsara rosters for enabled fleets.

    Motive (and any adapter raising ``NotImplementedError``) is skipped.
    """
    geotab: BaseTelematicsAdapter | None = ctx.get("geotab_adapter")
    samsara: BaseTelematicsAdapter | None = ctx.get("samsara_adapter")

    adapters_by_provider: dict[str, BaseTelematicsAdapter] = {}
    if geotab is not None:
        adapters_by_provider["geotab"] = geotab
    if samsara is not None:
        adapters_by_provider["samsara"] = samsara

    fleets = await list_enabled_fleets()
    results: list[dict[str, Any]] = []

    for fleet in fleets:
        adapter = adapters_by_provider.get(fleet.provider)
        if adapter is None:
            logger.debug(
                "No live adapter for provider=%s fleet=%s — skip roster sync",
                fleet.provider,
                fleet.fleet_id,
            )
            results.append(
                {
                    "provider": fleet.provider,
                    "tenant_id": fleet.fleet_id,
                    "skipped": True,
                    "reason": "adapter_unavailable",
                    "upserted": 0,
                }
            )
            continue
        try:
            result = await sync_fleet_roster(adapter, tenant_id=fleet.fleet_id)
            results.append(result)
        except Exception as exc:
            logger.error(
                "Roster sync failed provider=%s tenant=%s: %s",
                fleet.provider,
                fleet.fleet_id,
                exc,
            )
            results.append(
                {
                    "provider": fleet.provider,
                    "tenant_id": fleet.fleet_id,
                    "skipped": True,
                    "reason": "error",
                    "error": str(exc),
                    "upserted": 0,
                }
            )

    upserted_total = sum(int(r.get("upserted") or 0) for r in results)
    logger.info(
        "Roster sync complete: fleets=%d upserted=%d",
        len(results),
        upserted_total,
    )
    return {"fleets": results, "upserted_total": upserted_total}
