"""Ruleset B — federal 150 air-mile short-haul (§ 395.1(e) / PDF §4)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from app.domains.engine.calculators import (
    check_driving_limit,
    check_restart,
    check_weekly_cycle,
)
from app.domains.engine.findings import evaluate_findings, resolve_day_annotations
from app.domains.engine.packs.fmcsa_us_property import fmcsa_us_property_pack
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
from app.domains.engine.short_haul import (
    assess_short_haul_exemption,
    eld_8_in_30_findings,
    exemption_findings,
)
from app.domains.engine.state_machine import run_state_machine

logger = logging.getLogger("dcw.engine.packs.fmcsa_us_short_haul")

PACK_ID = "fmcsa_us_short_haul"
PACK_VERSION = "fmcsa-us-short-haul@1.0.0"


class FmcsaUsShortHaulPack:
    """Federal short-haul pack: exemption test, break suppression, A fallback."""

    pack_id: str = PACK_ID
    ruleset: RulesetId = RulesetId.B
    implemented: bool = True

    def evaluate(
        self,
        timeline: DriverTimeline,
        inputs_hash: str,
        *,
        version: str,
        weekly_duty_seconds: float = 0.0,
        as_of: Optional[datetime] = None,
        profile: DriverProfile,
        gps_fixes: Optional[Sequence[GpsFix]] = None,
        short_haul_failure_days_30: int = 0,
        day_annotations: Optional[DayAnnotations] = None,
        adverse_driving: Optional[bool] = None,
        sixteen_hour_exception: Optional[bool] = None,
    ) -> ComplianceResult:
        """Evaluate Ruleset B; fall back to full Ruleset A when exemption fails.

        ``short_haul_failure_days_30`` is the effective rolling failure-day count
        used for ELD 8-in-30 alerts (caller bumps when today is a new failure).
        """
        del version  # B binds its own SemVer pack id
        now = as_of if as_of is not None else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

        eval_timeline = truncate_timeline_to(timeline, now) if as_of is not None else timeline
        fixes: Sequence[GpsFix] = gps_fixes or ()
        annotations = resolve_day_annotations(
            day_annotations,
            adverse_driving=adverse_driving,
            sixteen_hour_exception=sixteen_hour_exception,
        )

        logger.debug(
            "Evaluating Ruleset B (%s) driver=%s events=%d gps=%d cdl=%s",
            PACK_VERSION,
            timeline.driver_id,
            len(eval_timeline.events),
            len(fixes),
            profile.cdl_required,
        )

        state = run_state_machine(eval_timeline)
        assessment = assess_short_haul_exemption(
            profile=profile,
            state=state,
            gps_fixes=fixes,
            as_of=now,
        )

        if not assessment.ok:
            a_result = fmcsa_us_property_pack.evaluate(
                eval_timeline,
                inputs_hash,
                version=PACK_VERSION,
                weekly_duty_seconds=weekly_duty_seconds,
                as_of=now,
                profile=profile,
                gps_fixes=fixes,
                short_haul_failure_days_30=short_haul_failure_days_30,
                day_annotations=annotations,
            )
            findings: List[Violation] = []
            findings.extend(exemption_findings(assessment, now=now))
            findings.extend(
                eld_8_in_30_findings(short_haul_failure_days_30, now=now)
            )
            findings.extend(a_result.violations)

            result = ComplianceResult(
                driver_id=timeline.driver_id,
                tenant_id=timeline.tenant_id,
                evaluated_at=now,
                rule_pack_version=PACK_VERSION,
                inputs_hash=inputs_hash,
                driving_remaining_seconds=a_result.driving_remaining_seconds,
                duty_window_remaining_seconds=a_result.duty_window_remaining_seconds,
                break_required=a_result.break_required,
                weekly_hours_used=a_result.weekly_hours_used,
                weekly_hours_remaining=a_result.weekly_hours_remaining,
                violations=findings,
                selected_ruleset=RulesetId.B,
                ruleset_status=RulesetStatus.IMPLEMENTED,
                ruleset_pack_id=self.pack_id,
            )
            logger.info(
                "Ruleset B fallback→A driver=%s reason=%s violations=%d",
                timeline.driver_id,
                None if assessment.reason is None else assessment.reason.value,
                len(findings),
            )
            return result

        # Exemption holds: 11h + 60/70 + restart; suppress 30-min break.
        # Duty remaining tracks the short-haul release window (14h CDL / 16h non-CDL).
        all_violations: List[Violation] = []

        driving_remaining, drive_violations = check_driving_limit(state, now)
        all_violations.extend(drive_violations)

        hours_used, hours_remaining, weekly_violations = check_weekly_cycle(
            weekly_duty_seconds, now
        )
        all_violations.extend(weekly_violations)

        restart_violations = check_restart(state, now)
        all_violations.extend(restart_violations)

        # Prior failure streak may still warrant ELD alerts even when today holds.
        all_violations.extend(
            eld_8_in_30_findings(short_haul_failure_days_30, now=now)
        )
        # PC/YM/form findings; federal adverse/16h notices omitted under B release path
        all_violations.extend(
            evaluate_findings(
                timeline=eval_timeline,
                state=state,
                annotations=annotations,
                gps_fixes=fixes,
                now=now,
                include_federal_exceptions=False,
            )
        )

        duty_remaining = max(
            0.0,
            assessment.release_limit_seconds - assessment.duty_elapsed_seconds,
        )

        result = ComplianceResult(
            driver_id=timeline.driver_id,
            tenant_id=timeline.tenant_id,
            evaluated_at=now,
            rule_pack_version=PACK_VERSION,
            inputs_hash=inputs_hash,
            driving_remaining_seconds=driving_remaining,
            duty_window_remaining_seconds=duty_remaining,
            break_required=False,
            weekly_hours_used=hours_used,
            weekly_hours_remaining=hours_remaining,
            violations=all_violations,
            selected_ruleset=RulesetId.B,
            ruleset_status=RulesetStatus.IMPLEMENTED,
            ruleset_pack_id=self.pack_id,
        )
        logger.info(
            "Ruleset B ok driver=%s compliant=%s radius_max=%.1f",
            timeline.driver_id,
            result.is_compliant,
            assessment.max_radius_air_miles,
        )
        return result


fmcsa_us_short_haul_pack = FmcsaUsShortHaulPack()
