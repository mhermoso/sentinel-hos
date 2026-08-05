"""ARQ compliance sweeper — scans active drivers and evaluates compliance.

Triggered on a cron schedule after each ingestion cycle. For each enabled
fleet (isolated — one fleet's failure never stops the others):
  1. Reads ``set:active_drivers:{fleet_id}`` from Redis.
  2. For each driver: fetches timeline from PostgreSQL.
  3. Runs the versioned rule pack.
  4. Persists the audit record.
  5. Publishes violations to Redis ``compliance_alerts`` pub/sub channel.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.redis import COMPLIANCE_ALERTS_CHANNEL, publish_event
from app.core.security import compute_inputs_hash
from app.domains.engine.replay import (
    WEEKLY_DUTY_LOOKBACK_BUFFER_DAYS,
    compute_weekly_duty_seconds,
    truncate_timeline_to,
)
from app.domains.engine.repository import EngineRepository
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import NON_TELEPHONY_FINDINGS, GpsFix, ViolationType
from app.domains.engine.short_haul import (
    assess_short_haul_exemption,
    home_terminal_day,
)
from app.domains.engine.short_haul_counter import (
    get_short_haul_failure_days,
    record_short_haul_failure_day,
)
from app.domains.engine.state_machine import run_state_machine
from app.domains.ingestion.fleets import list_enabled_fleets
from app.domains.ingestion.repository import IngestionRepository

logger = logging.getLogger("dcw.engine.sweeper")

_rule_pack = RulePack(version=settings.DEFAULT_RULE_PACK_VERSION)

# Dashboard/audit-only; do not dispatch telephony for risk / form findings.
_NON_TELEPHONY_VIOLATIONS = NON_TELEPHONY_FINDINGS


async def sweep_active_drivers(ctx: dict[str, Any]) -> dict[str, Any]:
    """ARQ task — evaluate compliance for every enabled fleet's active drivers.

    Designed to run shortly after each ingestion poll cycle completes. Fleets
    are swept sequentially and independently: one fleet's failure is logged
    and never aborts the others.
    """
    fleets = await list_enabled_fleets()
    if not fleets:
        logger.info("No enabled fleets to sweep")
        return {"drivers_swept": 0, "violations_published": 0, "fleets": {}}

    total_swept = 0
    total_published = 0
    per_fleet: dict[str, dict[str, int]] = {}

    for fleet in fleets:
        try:
            fleet_result = await _sweep_fleet(fleet.fleet_id)
        except Exception:
            logger.exception("Sweeper failed for fleet %s", fleet.fleet_id)
            per_fleet[fleet.fleet_id] = {"drivers_swept": 0, "violations_published": 0, "errors": 1}
            continue
        per_fleet[fleet.fleet_id] = fleet_result
        total_swept += fleet_result["drivers_swept"]
        total_published += fleet_result["violations_published"]

    logger.info(
        "Sweep complete: %d fleet(s), %d drivers evaluated, %d violation events published",
        len(fleets),
        total_swept,
        total_published,
    )
    return {
        "drivers_swept": total_swept,
        "violations_published": total_published,
        "fleets": per_fleet,
    }


async def _sweep_fleet(tenant_id: str) -> dict[str, int]:
    """Evaluate compliance for one fleet's currently active drivers."""
    driver_ids = await IngestionRepository.get_active_driver_ids(tenant_id)

    if not driver_ids:
        logger.info("No active drivers to sweep for fleet %s", tenant_id)
        return {"drivers_swept": 0, "violations_published": 0}

    swept = 0
    violations_published = 0
    lookback_days = settings.WEEKLY_CYCLE_DAYS + WEEKLY_DUTY_LOOKBACK_BUFFER_DAYS

    async with async_session_factory() as session:
        repo = EngineRepository(session)
        ing_repo = IngestionRepository(session)

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
                now = datetime.now(UTC)
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

                # 4. Load profile + GPS (short-haul + YM heuristics) / annotations
                profile = await repo.get_driver_profile(tenant_id, driver_id)
                day_annotations = await repo.get_day_annotations(
                    tenant_id,
                    driver_id,
                    as_of=now,
                    home_terminal_timezone=profile.home_terminal_timezone,
                )
                gps_start = now - timedelta(days=2)
                crumbs = await ing_repo.get_gps_breadcrumbs_for_driver(
                    tenant_id=tenant_id,
                    driver_id=driver_id,
                    start_utc=gps_start,
                    end_utc=now + timedelta(seconds=1),
                )
                gps_fixes: list[GpsFix] = [
                    GpsFix(
                        latitude=float(c.latitude),
                        longitude=float(c.longitude),
                        timestamp=c.event_timestamp,
                        speed_kmh=(
                            float(c.speed_kmh) if c.speed_kmh is not None else None
                        ),
                    )
                    for c in crumbs
                ]
                failure_days_30 = 0
                if profile.short_haul_eligible:
                    prior_days = await get_short_haul_failure_days(
                        tenant_id,
                        driver_id,
                        as_of=now,
                        home_terminal_timezone=profile.home_terminal_timezone,
                    )
                    eval_timeline = truncate_timeline_to(timeline, now)
                    assessment = assess_short_haul_exemption(
                        profile=profile,
                        state=run_state_machine(eval_timeline),
                        gps_fixes=gps_fixes,
                        as_of=now,
                    )
                    today = home_terminal_day(now, profile.home_terminal_timezone)
                    failure_days_30 = len(prior_days)
                    if not assessment.ok and today not in prior_days:
                        failure_days_30 += 1

                result = _rule_pack.evaluate(
                    timeline=timeline,
                    inputs_hash=inputs_hash,
                    weekly_duty_seconds=weekly_seconds,
                    as_of=now,
                    profile=profile,
                    gps_fixes=gps_fixes,
                    short_haul_failure_days_30=failure_days_30,
                    day_annotations=day_annotations,
                )

                if profile.short_haul_eligible and any(
                    v.violation_type == ViolationType.EXEMPTION_LOST
                    for v in result.violations
                ):
                    await record_short_haul_failure_day(
                        tenant_id,
                        driver_id,
                        as_of=now,
                        home_terminal_timezone=profile.home_terminal_timezone,
                    )

                # 5. Persist audit record
                await repo.persist_audit_record(result)
                await session.commit()

                swept += 1

                # 6. Publish violations to Redis pub/sub (skip non-telephony findings)
                if result.violations:
                    for violation in result.violations:
                        if violation.violation_type in _NON_TELEPHONY_VIOLATIONS:
                            logger.info(
                                "Skipping telephony for %s driver=%s ruleset=%s",
                                violation.violation_type.value,
                                driver_id,
                                result.selected_ruleset,
                            )
                            continue
                        event_payload = json.dumps(
                            {
                                "tenant_id": tenant_id,
                                "driver_id": driver_id,
                                "violation": violation.model_dump(mode="json"),
                                "compliance_result": {
                                    "driving_remaining_seconds": result.driving_remaining_seconds,
                                    "duty_window_remaining_seconds": result.duty_window_remaining_seconds,
                                    "break_required": result.break_required,
                                    "selected_ruleset": (
                                        result.selected_ruleset.value
                                        if result.selected_ruleset
                                        else None
                                    ),
                                    "ruleset_status": (
                                        result.ruleset_status.value
                                        if result.ruleset_status
                                        else None
                                    ),
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
        "Fleet %s sweep: %d drivers evaluated, %d violation events published",
        tenant_id,
        swept,
        violations_published,
    )
    return {
        "drivers_swept": swept,
        "violations_published": violations_published,
    }
