"""ARQ compliance sweeper — scans active drivers and evaluates compliance.

Triggered on a cron schedule after each ingestion cycle:
  1. Reads ``set:active_drivers`` from Redis.
  2. For each driver: fetches timeline from PostgreSQL.
  3. Runs the versioned rule pack.
  4. Persists the audit record.
  5. Publishes violations to Redis ``compliance_alerts`` pub/sub channel.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.redis import COMPLIANCE_ALERTS_CHANNEL, publish_event
from app.core.security import compute_inputs_hash
from app.domains.engine.replay import WEEKLY_DUTY_LOOKBACK_BUFFER_DAYS, compute_weekly_duty_seconds
from app.domains.engine.repository import EngineRepository
from app.domains.engine.rule_pack import RulePack
from app.domains.ingestion.repository import IngestionRepository

logger = logging.getLogger("dcw.engine.sweeper")

_rule_pack = RulePack(version=settings.DEFAULT_RULE_PACK_VERSION)


async def sweep_active_drivers(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """ARQ task — evaluate compliance for all currently active drivers.

    Designed to run shortly after each ingestion poll cycle completes.
    """
    tenant_id = settings.GEOTAB_DATABASE
    driver_ids = await IngestionRepository.get_active_driver_ids()

    if not driver_ids:
        logger.info("No active drivers to sweep")
        return {"drivers_swept": 0}

    swept = 0
    violations_published = 0
    lookback_days = settings.WEEKLY_CYCLE_DAYS + WEEKLY_DUTY_LOOKBACK_BUFFER_DAYS

    async with async_session_factory() as session:
        repo = EngineRepository(session)

        for driver_id in driver_ids:
            try:
                # 1. Fetch timeline (extended lookback for 34h restart detection)
                timeline = await repo.get_driver_timeline(
                    tenant_id=tenant_id,
                    driver_id=driver_id,
                    lookback_days=lookback_days,
                )

                if not timeline.events:
                    logger.debug("No events for driver %s — skipping", driver_id)
                    continue

                # 2. Compute weekly duty seconds for 60/70h rule
                now = datetime.now(timezone.utc)
                weekly_seconds = compute_weekly_duty_seconds(
                    timeline.events,
                    as_of=now,
                    cycle_days=settings.WEEKLY_CYCLE_DAYS,
                )

                # 3. Compute inputs hash for audit linkage
                inputs_hash = compute_inputs_hash(
                    {
                        "tenant_id": tenant_id,
                        "driver_id": driver_id,
                        "event_count": len(timeline.events),
                        "last_event": (
                            timeline.events[-1].timestamp.isoformat()
                            if timeline.events
                            else ""
                        ),
                    }
                )

                # 4. Evaluate rule pack
                result = _rule_pack.evaluate(
                    timeline=timeline,
                    inputs_hash=inputs_hash,
                    weekly_duty_seconds=weekly_seconds,
                )

                # 5. Persist audit record
                await repo.persist_audit_record(result)
                await session.commit()

                swept += 1

                # 6. Publish violations to Redis pub/sub
                if result.violations:
                    for violation in result.violations:
                        event_payload = json.dumps(
                            {
                                "tenant_id": tenant_id,
                                "driver_id": driver_id,
                                "violation": violation.model_dump(mode="json"),
                                "compliance_result": {
                                    "driving_remaining_seconds": result.driving_remaining_seconds,
                                    "duty_window_remaining_seconds": result.duty_window_remaining_seconds,
                                    "break_required": result.break_required,
                                },
                            },
                            default=str,
                        )
                        await publish_event(COMPLIANCE_ALERTS_CHANNEL, event_payload)
                        violations_published += 1

            except Exception as exc:
                logger.error(
                    "Sweeper error for driver %s: %s", driver_id, exc, exc_info=True
                )
                continue

    logger.info(
        "Sweep complete: %d drivers evaluated, %d violation events published",
        swept,
        violations_published,
    )
    return {
        "drivers_swept": swept,
        "violations_published": violations_published,
    }
