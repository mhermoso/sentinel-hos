"""Federal short-haul exemption tests (§ 395.1(e) / PDF §4) — pure helpers.

Used by Ruleset B (and later D). Callers supply mapped GPS fixes; this module
never queries PostgreSQL (ADR-007 store stays behind the repository boundary).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from app.domains.engine.geo import (
    RETURN_RADIUS_AIR_MILES,
    SHORT_HAUL_RADIUS_AIR_MILES,
    distance_from_origin_air_miles,
)
from app.domains.engine.schemas import (
    DriverProfile,
    GpsFix,
    Violation,
    ViolationSeverity,
    ViolationType,
)
from app.domains.engine.state_machine import StateMachineResult
from app.domains.ingestion.schemas import CanonicalDutyStatus

# CDL short-haul: return + release within 14 consecutive hours (§ 395.1(e)(1)).
CDL_RELEASE_LIMIT_SECONDS: float = 14 * 3600.0
# Non-CDL short-haul: return + release within 16 consecutive hours (§ 395.1(e)(2) / PDF §4.2).
NON_CDL_RELEASE_LIMIT_SECONDS: float = 16 * 3600.0

_REST_STATUSES = {
    CanonicalDutyStatus.OFF_DUTY.value,
    CanonicalDutyStatus.SLEEPER_BERTH.value,
    CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
}


class ExemptionFailReason(str, Enum):
    """Why the short-haul exemption does not hold for the evaluation day."""

    MISSING_WORK_REPORTING_LOCATION = "MISSING_WORK_REPORTING_LOCATION"
    MISSING_GPS_BREADCRUMBS = "MISSING_GPS_BREADCRUMBS"
    BEYOND_150_AIR_MILES = "BEYOND_150_AIR_MILES"
    RELEASE_WINDOW_EXCEEDED = "RELEASE_WINDOW_EXCEEDED"
    DID_NOT_RETURN = "DID_NOT_RETURN"


@dataclass(frozen=True)
class ExemptionAssessment:
    """Result of day-level short-haul condition checks."""

    ok: bool
    reason: ExemptionFailReason | None = None
    detail: str = ""
    max_radius_air_miles: float = 0.0
    release_limit_seconds: float = CDL_RELEASE_LIMIT_SECONDS
    duty_elapsed_seconds: float = 0.0


def release_limit_seconds(cdl_required: bool) -> float:
    """CDL → 14h; non-CDL → 16h (PDF §4.2)."""
    return CDL_RELEASE_LIMIT_SECONDS if cdl_required else NON_CDL_RELEASE_LIMIT_SECONDS


def home_terminal_day(as_of: datetime, timezone_name: str) -> str:
    """ISO calendar date in the driver's home-terminal timezone."""
    tz = ZoneInfo(timezone_name)
    local = as_of.astimezone(tz) if as_of.tzinfo else as_of.replace(tzinfo=tz)
    return local.date().isoformat()


def assess_short_haul_exemption(
    *,
    profile: DriverProfile,
    state: StateMachineResult,
    gps_fixes: Sequence[GpsFix],
    as_of: datetime,
) -> ExemptionAssessment:
    """Test § 395.1(e) day conditions. Fail closed when inputs are missing."""
    limit = release_limit_seconds(profile.cdl_required)

    if profile.work_reporting_location is None:
        return ExemptionAssessment(
            ok=False,
            reason=ExemptionFailReason.MISSING_WORK_REPORTING_LOCATION,
            detail="Work-reporting location is required for short-haul air-mile checks.",
            release_limit_seconds=limit,
        )

    origin = (
        profile.work_reporting_location.latitude,
        profile.work_reporting_location.longitude,
    )

    duty_start = state.duty_window_start
    if duty_start is None and state.current_shift is not None:
        duty_start = state.current_shift.shift_start

    # No active duty tour — exemption conditions are not tested yet.
    if duty_start is None:
        return ExemptionAssessment(ok=True, release_limit_seconds=limit)

    duty_elapsed = max(0.0, (as_of - duty_start).total_seconds())
    window_fixes = [
        fix
        for fix in gps_fixes
        if duty_start <= fix.timestamp <= as_of
    ]

    if not window_fixes:
        return ExemptionAssessment(
            ok=False,
            reason=ExemptionFailReason.MISSING_GPS_BREADCRUMBS,
            detail=(
                "No GPS breadcrumbs during the duty window; "
                "short-haul radius cannot be verified (fail closed)."
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
                    f"from work-reporting location (limit {SHORT_HAUL_RADIUS_AIR_MILES:.0f})."
                ),
                max_radius_air_miles=dist,
                release_limit_seconds=limit,
                duty_elapsed_seconds=duty_elapsed,
            )

    released = (
        state.current_status in _REST_STATUSES
        and state.consecutive_rest_seconds > 0.0
        and duty_elapsed > 0.0
    )

    if duty_elapsed > limit and not released:
        hours = limit / 3600.0
        return ExemptionAssessment(
            ok=False,
            reason=ExemptionFailReason.RELEASE_WINDOW_EXCEEDED,
            detail=(
                f"Driver not released within {hours:.0f} consecutive hours "
                f"({duty_elapsed / 3600:.2f}h elapsed; "
                f"{'CDL' if profile.cdl_required else 'non-CDL'} short-haul)."
            ),
            max_radius_air_miles=max_radius,
            release_limit_seconds=limit,
            duty_elapsed_seconds=duty_elapsed,
        )

    if released:
        # Release time ≈ start of current rest block.
        release_at = as_of
        if state.consecutive_rest_seconds > 0.0:
            release_at = as_of - timedelta(seconds=state.consecutive_rest_seconds)
        tour_seconds = max(0.0, (release_at - duty_start).total_seconds())
        if tour_seconds > limit:
            hours = limit / 3600.0
            return ExemptionAssessment(
                ok=False,
                reason=ExemptionFailReason.RELEASE_WINDOW_EXCEEDED,
                detail=(
                    f"Released after {tour_seconds / 3600:.2f}h "
                    f"(limit {hours:.0f}h for "
                    f"{'CDL' if profile.cdl_required else 'non-CDL'} short-haul)."
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


def exemption_findings(
    assessment: ExemptionAssessment,
    *,
    now: datetime,
) -> list[Violation]:
    """Build EXEMPTION_LOST + RODS_REQUIRED when the exemption fails."""
    if assessment.ok or assessment.reason is None:
        return []

    reason = assessment.reason.value
    detail = assessment.detail or reason
    return [
        Violation(
            violation_type=ViolationType.EXEMPTION_LOST,
            severity=ViolationSeverity.VIOLATION,
            rule_ref="§ 395.1(e)",
            description=f"Short-haul exemption lost: {detail}",
            detected_at=now,
            overage_seconds=max(
                0.0,
                assessment.duty_elapsed_seconds - assessment.release_limit_seconds,
            ),
        ),
        Violation(
            violation_type=ViolationType.RODS_REQUIRED,
            severity=ViolationSeverity.VIOLATION,
            rule_ref="§ 395.1(e) / § 395.8",
            description=(
                "Record of Duty Status required for this day after short-haul "
                f"exemption failure ({reason})."
            ),
            detected_at=now,
        ),
    ]


def eld_8_in_30_findings(
    failure_days_in_30: int,
    *,
    now: datetime,
) -> list[Violation]:
    """Alerts for rolling exemption-failure days: warn@5 / urgent@7 / viol@9+."""
    if failure_days_in_30 < 5:
        return []

    if failure_days_in_30 >= 9:
        severity = ViolationSeverity.CRITICAL
        stage = "ELD required (9+ exemption-failure days in 30)"
    elif failure_days_in_30 >= 7:
        severity = ViolationSeverity.VIOLATION
        stage = "approaching ELD mandate (7+ exemption-failure days in 30)"
    else:
        severity = ViolationSeverity.WARNING
        stage = "short-haul failure streak (5+ days in 30)"

    return [
        Violation(
            violation_type=ViolationType.ELD_REQUIRED_8_IN_30,
            severity=severity,
            rule_ref="§ 395.8 / ELD 8-in-30",
            description=(
                f"Driver has {failure_days_in_30} short-haul exemption-failure "
                f"day(s) in the rolling 30-day window — {stage}."
            ),
            detected_at=now,
            overage_seconds=float(max(0, failure_days_in_30 - 8) * 86400),
        )
    ]
