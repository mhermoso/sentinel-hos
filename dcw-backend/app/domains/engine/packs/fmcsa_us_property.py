"""Ruleset A — FMCSA U.S. property-carrying interstate (49 CFR Part 395)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.domains.engine.calculators import (
    check_driving_limit,
    check_duty_window,
    check_rest_break,
    check_restart,
    check_weekly_cycle,
)
from app.domains.engine.findings import (
    evaluate_findings,
    resolve_day_annotations,
    resolve_federal_limits,
)
from app.domains.engine.replay import truncate_timeline_to
from app.domains.engine.schemas import (
    ComplianceResult,
    DayAnnotations,
    DriverProfile,
    DriverTimeline,
    GpsFix,
    RulesetId,
    RulesetStatus,
    Violation,
)
from app.domains.engine.state_machine import run_state_machine

logger = logging.getLogger("dcw.engine.packs.fmcsa_us_property")

PACK_ID = "fmcsa_us_property"


class FmcsaUsPropertyPack:
    """Fully implemented Ruleset A pack (federal interstate property-carrying)."""

    pack_id: str = PACK_ID
    ruleset: RulesetId = RulesetId.A
    implemented: bool = True

    def evaluate(
        self,
        timeline: DriverTimeline,
        inputs_hash: str,
        *,
        version: str,
        weekly_duty_seconds: float = 0.0,
        as_of: datetime | None = None,
        profile: DriverProfile,
        gps_fixes: Sequence[GpsFix] | None = None,
        short_haul_failure_days_30: int = 0,
        day_annotations: DayAnnotations | None = None,
        adverse_driving: bool | None = None,
        sixteen_hour_exception: bool | None = None,
    ) -> ComplianceResult:
        """Run federal HOS calculators, then Phase 6 findings."""
        del short_haul_failure_days_30  # Ruleset A does not use 8-in-30
        now = as_of if as_of is not None else datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        else:
            now = now.astimezone(UTC)

        eval_timeline = truncate_timeline_to(timeline, now) if as_of is not None else timeline
        annotations = resolve_day_annotations(
            day_annotations,
            adverse_driving=adverse_driving,
            sixteen_hour_exception=sixteen_hour_exception,
        )
        max_driving, max_duty, _applied = resolve_federal_limits(annotations)
        fixes: Sequence[GpsFix] = gps_fixes or ()

        logger.debug(
            "Evaluating Ruleset A (%s) driver=%s events=%d as_of=%s cycle=%s "
            "adverse=%s sixteen=%s",
            version,
            timeline.driver_id,
            len(eval_timeline.events),
            now.isoformat(),
            profile.cycle.value,
            annotations.adverse_driving,
            annotations.sixteen_hour_exception,
        )

        state = run_state_machine(eval_timeline)

        all_violations: list[Violation] = []

        driving_remaining, drive_violations = check_driving_limit(
            state, now, max_driving_seconds=max_driving
        )
        all_violations.extend(drive_violations)

        duty_remaining, duty_violations = check_duty_window(
            state, now, max_duty_window_seconds=max_duty
        )
        all_violations.extend(duty_violations)

        break_required, break_violations = check_rest_break(state, now)
        all_violations.extend(break_violations)

        hours_used, hours_remaining, weekly_violations = check_weekly_cycle(
            weekly_duty_seconds, now
        )
        all_violations.extend(weekly_violations)

        restart_violations = check_restart(state, now)
        all_violations.extend(restart_violations)

        all_violations.extend(
            evaluate_findings(
                timeline=eval_timeline,
                state=state,
                annotations=annotations,
                gps_fixes=fixes,
                now=now,
                include_federal_exceptions=True,
            )
        )

        result = ComplianceResult(
            driver_id=timeline.driver_id,
            tenant_id=timeline.tenant_id,
            evaluated_at=now,
            rule_pack_version=version,
            inputs_hash=inputs_hash,
            driving_remaining_seconds=driving_remaining,
            duty_window_remaining_seconds=duty_remaining,
            break_required=break_required,
            weekly_hours_used=hours_used,
            weekly_hours_remaining=hours_remaining,
            violations=all_violations,
            selected_ruleset=RulesetId.A,
            ruleset_status=RulesetStatus.IMPLEMENTED,
            ruleset_pack_id=self.pack_id,
        )

        logger.info(
            "Ruleset A result driver=%s compliant=%s violations=%d",
            timeline.driver_id,
            result.is_compliant,
            len(all_violations),
        )
        return result


fmcsa_us_property_pack = FmcsaUsPropertyPack()
