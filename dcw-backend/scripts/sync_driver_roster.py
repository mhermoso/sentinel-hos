#!/usr/bin/env python3
"""One-shot sync of Geotab + Samsara driver rosters into ``driver_roster``.

Product path used by ``make sync-roster`` and the ARQ cron
(``sync_driver_rosters``). Motive is skipped until its adapter is live.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import settings
from app.core.database import init_db
from app.domains.ingestion.adapters.geotab import GeotabAdapter
from app.domains.ingestion.adapters.samsara import SamsaraAdapter
from app.domains.ingestion.fleets import sync_fleets_to_db
from app.domains.ingestion.roster_sync import sync_driver_rosters

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dcw.scripts.sync_driver_roster")


async def _build_ctx() -> dict[str, Any]:
    ctx: dict[str, Any] = {"geotab_adapter": None, "samsara_adapter": None}

    if settings.GEOTAB_DATABASE and settings.GEOTAB_USERNAME and settings.GEOTAB_PASSWORD:
        try:
            adapter = GeotabAdapter()
            await adapter.connect()
            ctx["geotab_adapter"] = adapter
            logger.info("Geotab adapter ready")
        except Exception as exc:
            logger.error("Geotab connect failed: %s", exc)

    if settings.SAMSARA_API_TOKEN:
        try:
            adapter = SamsaraAdapter()
            await adapter.connect()
            ctx["samsara_adapter"] = adapter
            logger.info("Samsara adapter ready (fleet_id=%s)", adapter.fleet_id)
        except Exception as exc:
            logger.error("Samsara connect failed: %s", exc)

    return ctx


async def main() -> int:
    await init_db()
    await sync_fleets_to_db()
    ctx = await _build_ctx()
    if ctx["geotab_adapter"] is None and ctx["samsara_adapter"] is None:
        logger.error("No telematics adapters configured — nothing to sync")
        return 1

    result = await sync_driver_rosters(ctx)
    for fleet_result in result.get("fleets", []):
        logger.info("Fleet result: %s", fleet_result)
    logger.info("Upserted total: %s", result.get("upserted_total"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
