"""Ruleset D — Texas 150 air-mile short-haul (37 TAC §4.12(a)(4)–(5))."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from app.domains.engine.geo import (
    RETURN_RADIUS_AIR_MILES,
    SHORT_HAUL_RADIUS_AIR_MILES,
    distance_from_origin_air_miles,
)
from app.domains.engine.packs.tx_intrastate import evaluate_tx_intrastate
from app.domains.engine.replay import truncate_timeline_to
from app.domains.engine.schemas import (
    ComplianceResult,
    DayAnnotations,
    DriverProfile,
    DriverTimeline,
    GpsFix,
    RulesetId,
    Violation,
    ViolationSeverity,
    ViolationType,
)
from app.domains.engine.short_haul import ExemptionAssessment, ExemptionFailReason
from app.domains.engine.tx_state_machine import (
    TX_QUALIFYING_OFF_DUTY_SECONDS,
    run_tx_state_machine,
)
from app.domains.ingestion.schemas import CanonicalDutyStatus

logger = logging.getLogger("dcw.engine.packs.tx_short_haul")

PACK_ID = "tx_short_haul"
PACK_VERSION = "tx-short-haul@1.0.0"

# Texas short-haul: return + release within 12 consecutive hours (PDF §6).
TX_SHORT_HAUL_RELEASE_SECONDS: float = 12 * 3600.0

_REST_STATUS_VALUES = {
    CanonicalDutyStatus.OFF_DUTY.value,
    CanonicalDutyStatus.SLEEPER_BERTH.value,
    CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
}


def assess_tx_short_haul_exemption(
    *,
    profile: DriverProfile,
    timeline: DriverTimeline,
    gps_fixes: Sequence[GpsFix],
    as_of: datetime,
) -> ExemptionAssessment:
    """Verify TX 150 air-mile + 12h release + 8h off between tours (PDF §6)."""
    limit = TX_SHORT_HAUL_RELEASE_SECONDS

    if profile.work_reporting_location is None:
        return ExemptionAssessment(
            ok=False,
            reason=ExemptionFailReason.MISSING_WORK_REPORTING_LOCATION,
            detail="Work-reporting location is required for Texas short-haul checks.",
            release_limit_seconds=limit,
        )

    state = run_tx_state_machine(timeline)
    origin = (
        profile.work_reporting_location.latitude,
        profile.work_reporting_location.longitude,
    )

    duty_start = state.duty_window_start
    if duty_start is None and state.current_shift is not None:
        duty_start = state.current_shift.shift_start

    if duty_start is None:
        return ExemptionAssessment(ok=True, release_limit_seconds=limit)

    # 8h off between tours: a new TX shift requires ≥8h qualifying rest.
    # If the current shift's qualifying rest was shorter, exemption fails.
    if state.current_shift is not None:
        rest_before = (
            state.current_shift.shift_start - state.current_shift.qualifying_rest_before
        ).total_seconds()
        if rest_before < TX_QUALIFYING_OFF_DUTY_SECONDS:
            return ExemptionAssessment(
                ok=False,
                reason=ExemptionFailReason.RELEASE_WINDOW_EXCEEDED,
                detail=(
                    f"Less than 8 consecutive hours off between Texas short-haul "
                    f"tours ({rest_before / 3600:.2f}h)."
                ),
                release_limit_seconds=limit,
                duty_elapsed_seconds=state.duty_window_elapsed_seconds,
            )

    duty_elapsed = max(0.0, (as_of - duty_start).total_seconds())
    window_fixes = [fix for fix in gps_fixes if duty_start <= fix.timestamp <= as_of]

    if not window_fixes:
        return ExemptionAssessment(
            ok=False,
            reason=ExemptionFailReason.MISSING_GPS_BREADCRUMBS,
            detail=(
                "No GPS breadcrumbs during the Texas short-haul duty window; "
                "150 air-mile radius cannot be verified (fail closed)."
            ),
            release_limit_seconds=limit,
            duty_elapsed_seconds=duty_elapsed,
        )

    max_radius = 0.0
    for fix in window_fixes:
        dist = distance_from_origin_air_miles(origin, fix.latitude, fix.longitude)
        max_radius = max(max_radius, dist)
        if dist > SHORT_HAUL_RADIUS_AIR_MILES:
            return ExemptionAssessment(
                ok=False,
                reason=ExemptionFailReason.BEYOND_150_AIR_MILES,
                detail=(
                    f"GPS fix at {fix.timestamp.isoformat()} is {dist:.1f} air-miles "
                    f"from work-reporting location "
                    f"(Texas limit {SHORT_HAUL_RADIUS_AIR_MILES:.0f})."
                ),
                max_radius_air_miles=dist,
                release_limit_seconds=limit,
                duty_elapsed_seconds=duty_elapsed,
            )

    released = (
        state.current_status in _REST_STATUS_VALUES
        and state.consecutive_rest_seconds > 0.0
        and duty_elapsed > 0.0
    )

    if duty_elapsed > limit and not released:
        return ExemptionAssessment(
            ok=False,
            reason=ExemptionFailReason.RELEASE_WINDOW_EXCEEDED,
            detail=(
                f"Driver not released within 12 consecutive hours "
                f"({duty_elapsed / 3600:.2f}h elapsed; Texas short-haul)."
            ),
            max_radius_air_miles=max_radius,
            release_limit_seconds=limit,
            duty_elapsed_seconds=duty_elapsed,
        )

    if released:
        from datetime import timedelta

        release_at = as_of - timedelta(seconds=state.consecutive_rest_seconds)
        tour_seconds = max(0.0, (release_at - duty_start).total_seconds())
        if tour_seconds > limit:
            return ExemptionAssessment(
                ok=False,
                reason=ExemptionFailReason.RELEASE_WINDOW_EXCEEDED,
                detail=(
                    f"Released after {tour_seconds / 3600:.2f}h "
                    f"(Texas short-haul limit 12h)."
                ),
                max_radius_air_miles=max_radius,
                release_limit_seconds=limit,
                duty_elapsed_seconds=tour_seconds,
            )

        last_fix = window_fixes[-1]
        return_dist = distance_from_origin_air_miles(
            origin, last_fix.latitude, last_fix.longitude
        )
        if return_dist > RETURN_RADIUS_AIR_MILES:
            return ExemptionAssessment(
                ok=False,
                reason=ExemptionFailReason.DID_NOT_RETURN,
                detail=(
                    f"Last GPS fix is {return_dist:.1f} air-miles from work-reporting "
                    f"location (return tolerance {RETURN_RADIUS_AIR_MILES:.0f})."
                ),
                max_radius_air_miles=max_radius,
                release_limit_seconds=limit,
                duty_elapsed_seconds=tour_seconds,
            )

    return ExemptionAssessment(
        ok=True,
        max_radius_air_miles=max_radius,
        release_limit_seconds=limit,
        duty_elapsed_seconds=duty_elapsed,
    )


def tx_exemption_findings(
    assessment: ExemptionAssessment,
    *,
    now: datetime,
) -> list[Violation]:
    """EXEMPTION_LOST + RODS_REQUIRED under 37 TAC short-haul failure."""
    if assessment.ok or assessment.reason is None:
        return []

    reason = assessment.reason.value
    detail = assessment.detail or reason
    return [
        Violation(
            violation_type=ViolationType.EXEMPTION_LOST,
            severity=ViolationSeverity.VIOLATION,
            rule_ref="37 TAC §4.12(a)(4)–(5)",
            description=f"Texas short-haul exemption lost: {detail}",
            detected_at=now,
            overage_seconds=max(
                0.0,
                assessment.duty_elapsed_seconds - assessment.release_limit_seconds,
            ),
        ),
        Violation(
            violation_type=ViolationType.RODS_REQUIRED,
            severity=ViolationSeverity.VIOLATION,
            rule_ref="37 TAC §4.12(a)(4)–(5)",
            description=(
                "Record of Duty Status required for this day after Texas "
                f"short-haul exemption failure ({reason})."
            ),
            detected_at=now,
        ),
    ]


class TxShortHaulPack:
    """Fully implemented Ruleset D — TX short-haul with parallel C clocks."""

    pack_id: str = PACK_ID
    ruleset: RulesetId = RulesetId.D
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
        del short_haul_failure_days_30  # federal 8-in-30; not a TX D requirement
        now = as_of if as_of is not None else datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        else:
            now = now.astimezone(UTC)

        pack_version = version if version.startswith("tx-") else PACK_VERSION
        eval_timeline = (
            truncate_timeline_to(timeline, now) if as_of is not None else timeline
        )
        fixes = list(gps_fixes or [])

        # Parallel Ruleset C accumulators always run under D.
        result = evaluate_tx_intrastate(
            timeline,
            inputs_hash,
            version=pack_version,
            weekly_duty_seconds=weekly_duty_seconds,
            as_of=as_of,
            profile=profile,
            selected_ruleset=RulesetId.D,
            pack_id=self.pack_id,
            gps_fixes=fixes,
            day_annotations=day_annotations,
            adverse_driving=adverse_driving,
            sixteen_hour_exception=sixteen_hour_exception,
        )

        assessment = assess_tx_short_haul_exemption(
            profile=profile,
            timeline=eval_timeline,
            gps_fixes=fixes,
            as_of=now,
        )

        extra: list[Violation] = []
        if not assessment.ok:
            # RODS relief only while exemption holds; on fail → full C + findings.
            extra = tx_exemption_findings(assessment, now=now)
            logger.info(
                "Ruleset D exemption lost driver=%s reason=%s",
                timeline.driver_id,
                None if assessment.reason is None else assessment.reason.value,
            )
        else:
            logger.debug(
                "Ruleset D exemption holds driver=%s max_radius=%.1f",
                timeline.driver_id,
                assessment.max_radius_air_miles,
            )

        if extra:
            merged = list(result.violations) + extra
            result = result.model_copy(update={"violations": merged})

        return result


tx_short_haul_pack = TxShortHaulPack()
