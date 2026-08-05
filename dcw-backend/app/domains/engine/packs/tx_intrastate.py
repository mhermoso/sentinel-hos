"""Ruleset C — Texas intrastate property-carrying (37 TAC §4.12)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.domains.engine.findings import evaluate_findings, resolve_day_annotations
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
from app.domains.engine.tx_calculators import (
    check_tx_driving_limit,
    check_tx_duty_limit,
    check_tx_weekly_cycle,
)
from app.domains.engine.tx_state_machine import run_tx_state_machine

logger = logging.getLogger("dcw.engine.packs.tx_intrastate")

PACK_ID = "tx_intrastate"
PACK_VERSION = "tx-intrastate@1.0.0"


def evaluate_tx_intrastate(
    timeline: DriverTimeline,
    inputs_hash: str,
    *,
    version: str,
    weekly_duty_seconds: float,
    as_of: datetime | None,
    profile: DriverProfile,
    selected_ruleset: RulesetId = RulesetId.C,
    pack_id: str = PACK_ID,
    gps_fixes: Sequence[GpsFix] | None = None,
    day_annotations: DayAnnotations | None = None,
    adverse_driving: bool | None = None,
    sixteen_hour_exception: bool | None = None,
) -> ComplianceResult:
    """Run Texas C clocks (shared by Rulesets C and D) + Phase 6 risk findings."""
    del profile  # cycle forced to TX 70/7 in calculators
    now = as_of if as_of is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)

    eval_timeline = truncate_timeline_to(timeline, now) if as_of is not None else timeline
    state = run_tx_state_machine(eval_timeline)
    annotations = resolve_day_annotations(
        day_annotations,
        adverse_driving=adverse_driving,
        sixteen_hour_exception=sixteen_hour_exception,
    )
    fixes: Sequence[GpsFix] = gps_fixes or ()

    all_violations: list[Violation] = []

    driving_remaining, drive_violations = check_tx_driving_limit(state, now)
    all_violations.extend(drive_violations)

    duty_remaining, duty_violations = check_tx_duty_limit(state, now)
    all_violations.extend(duty_violations)

    hours_used, hours_remaining, weekly_violations = check_tx_weekly_cycle(
        weekly_duty_seconds, now
    )
    all_violations.extend(weekly_violations)

    # No 30-minute break / federal adverse-16h clocks under Texas; still run
    # PC/YM/form risk findings (PC-after-exhaust uses federal window math).
    federal_state = run_state_machine(eval_timeline)
    all_violations.extend(
        evaluate_findings(
            timeline=eval_timeline,
            state=federal_state,
            annotations=annotations,
            gps_fixes=fixes,
            now=now,
            include_federal_exceptions=False,
        )
    )

    return ComplianceResult(
        driver_id=timeline.driver_id,
        tenant_id=timeline.tenant_id,
        evaluated_at=now,
        rule_pack_version=version,
        inputs_hash=inputs_hash,
        driving_remaining_seconds=driving_remaining,
        duty_window_remaining_seconds=duty_remaining,
        break_required=False,
        weekly_hours_used=hours_used,
        weekly_hours_remaining=hours_remaining,
        violations=all_violations,
        selected_ruleset=selected_ruleset,
        ruleset_status=RulesetStatus.IMPLEMENTED,
        ruleset_pack_id=pack_id,
    )


class TxIntrastatePack:
    """Fully implemented Ruleset C pack (Texas intrastate)."""

    pack_id: str = PACK_ID
    ruleset: RulesetId = RulesetId.C
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
        del short_haul_failure_days_30
        pack_version = version if version.startswith("tx-") else PACK_VERSION
        logger.debug(
            "Evaluating Ruleset C (%s) driver=%s events=%d",
            pack_version,
            timeline.driver_id,
            len(timeline.events),
        )
        result = evaluate_tx_intrastate(
            timeline,
            inputs_hash,
            version=pack_version,
            weekly_duty_seconds=weekly_duty_seconds,
            as_of=as_of,
            profile=profile,
            selected_ruleset=RulesetId.C,
            pack_id=self.pack_id,
            gps_fixes=gps_fixes,
            day_annotations=day_annotations,
            adverse_driving=adverse_driving,
            sixteen_hour_exception=sixteen_hour_exception,
        )
        logger.info(
            "Ruleset C result driver=%s compliant=%s violations=%d",
            timeline.driver_id,
            result.is_compliant,
            len(result.violations),
        )
        return result


tx_intrastate_pack = TxIntrastatePack()
