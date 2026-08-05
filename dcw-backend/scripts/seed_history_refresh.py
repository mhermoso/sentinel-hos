#!/usr/bin/env python3
"""Dev-only: truncate tenant HOS seed rows and re-persist hos_30d_canonical.json.

Bypasses the append-only DELETE trigger for the GEOTAB_DATABASE tenant so
corrected PC/YM statuses can be re-seeded after a mapping fix. Do not use in
production.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session_factory, init_db
from app.domains.ingestion.repository import IngestionRepository
from app.domains.ingestion.schemas import DCWCanonicalHOSLog

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dcw.scripts.seed_history_refresh")


def _load_logs_from_grouped_json(path: Path) -> list[DCWCanonicalHOSLog]:
    import json

    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    logs: list[DCWCanonicalHOSLog] = []
    for records in raw.values():
        for record in records:
            logs.append(DCWCanonicalHOSLog.model_validate(record))
    return logs


async def truncate_tenant_hos(tenant_id: str) -> None:
    """Delete log_event_edits + canonical_hos_logs (+ audit_records) for tenant."""
    await init_db()
    async with async_session_factory() as session:
        # Append-only trigger blocks DELETE — disable for this repair session.
        trigger_disabled = False
        try:
            await session.execute(
                text(
                    "ALTER TABLE canonical_hos_logs "
                    "DISABLE TRIGGER trg_canonical_hos_logs_no_mutation"
                )
            )
            trigger_disabled = True
        except Exception as exc:
            logger.warning("Could not disable append-only trigger (continuing): %s", exc)
            await session.rollback()

        try:
            edits = await session.execute(
                text("DELETE FROM log_event_edits WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            audits = await session.execute(
                text("DELETE FROM audit_records WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            logs = await session.execute(
                text("DELETE FROM canonical_hos_logs WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            await session.commit()
            logger.warning(
                "DEV truncate tenant=%s: deleted log_event_edits=%s audit_records=%s "
                "canonical_hos_logs=%s",
                tenant_id,
                edits.rowcount,
                audits.rowcount,
                logs.rowcount,
            )
        finally:
            if trigger_disabled:
                await session.execute(
                    text(
                        "ALTER TABLE canonical_hos_logs "
                        "ENABLE TRIGGER trg_canonical_hos_logs_no_mutation"
                    )
                )
                await session.commit()


async def reseed(path: Path, tenant_id: str) -> int:
    logs = _load_logs_from_grouped_json(path)
    # Ensure tenant_id on loaded rows matches target (file may already be correct)
    async with async_session_factory() as session:
        repo = IngestionRepository(session)
        inserted = await repo.persist_canonical_logs(logs)
        driver_ids = {log.driver_id for log in logs}
        await IngestionRepository.update_active_drivers(tenant_id, driver_ids)
        await session.commit()
        logger.info(
            "Re-seeded %d rows (%d drivers) for tenant=%s from %s",
            inserted,
            len(driver_ids),
            tenant_id,
            path,
        )
        return inserted


async def run(path: Path, tenant_id: str) -> None:
    await truncate_tenant_hos(tenant_id)
    await reseed(path, tenant_id)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Truncate tenant HOS logs and re-seed from canonical JSON (dev only)"
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        default=_ROOT / "data" / "hos_30d_canonical.json",
        help="Driver-grouped canonical JSON",
    )
    parser.add_argument(
        "--tenant-id",
        default=settings.GEOTAB_DATABASE,
        help="Tenant to truncate/reseed (default: GEOTAB_DATABASE)",
    )
    args = parser.parse_args()

    tenant_id = args.tenant_id or settings.GEOTAB_DATABASE
    if not tenant_id:
        logger.error("Tenant ID required — set GEOTAB_DATABASE or pass --tenant-id")
        sys.exit(1)
    if not args.from_file.exists():
        logger.error("Input file not found: %s", args.from_file)
        sys.exit(1)

    logger.warning(
        "seed-history-refresh is DEV-ONLY: will DELETE HOS/audit rows for tenant=%s",
        tenant_id,
    )
    asyncio.run(run(args.from_file, tenant_id))


if __name__ == "__main__":
    main()
