"""Phase 6 findings — exceptions, PC/YM abuse heuristics, form & manner (§ 395.8).

Invoked after clock evaluation. Findings persist on audit records; telephony
is suppressed for these types by default (see ``NON_TELEPHONY_FINDINGS``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from app.domains.engine.calculators import (
    MAX_DRIVING_SECONDS,
    MAX_DUTY_WINDOW_SECONDS,
)
from app.domains.engine.geo import haversine_air_miles
from app.domains.engine.schemas import (
    DayAnnotations,
    DriverTimeline,
    GpsFix,
    Violation,
    ViolationSeverity,
    ViolationType,
    WorkReportingLocation,
)
from app.domains.engine.state_machine import StateMachineResult
from app.domains.ingestion.schemas import CanonicalDutyStatus

logger = logging.getLogger("dcw.engine.findings")

# Adverse driving conditions (§ 395.1(b)) — 13h driving / 16h window for the day.
ADVERSE_MAX_DRIVING_SECONDS: float = 13 * 3600.0
ADVERSE_MAX_DUTY_WINDOW_SECONDS: float = 16 * 3600.0

# § 395.1(o) 16-hour short-haul exception — extend 14h → 16h; 11h drive unchanged.
SIXTEEN_HOUR_MAX_DUTY_WINDOW_SECONDS: float = 16 * 3600.0

# PC abuse heuristics
PC_ABUSE_DURATION_SECONDS: float = 3 * 3600.0
# ~3 statute miles closer to next load (air miles ≈ nmi; use ~2.6 nmi ≈ 3 mi)
PC_TOWARD_LOAD_AIR_MILES: float = 2.6

# YM falsification — highway speeds (>20 mph ≈ 32 km/h)
YM_HIGHWAY_SPEED_KMH: float = 32.0

# Form & manner
ELD_MALFUNCTION_PAPER_DAYS: int = 8

_DRIVING_TO_REST_EDIT_TARGETS = frozenset(
    {
        CanonicalDutyStatus.OFF_DUTY.value,
        CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
        CanonicalDutyStatus.YARD_MOVE.value,
    }
)


def resolve_day_annotations(
    day_annotations: Optional[DayAnnotations] = None,
    *,
    adverse_driving: Optional[bool] = None,
    sixteen_hour_exception: Optional[bool] = None,
) -> DayAnnotations:
    """Merge evaluate kwargs onto a DayAnnotations instance."""
    base = day_annotations or DayAnnotations()
    updates: dict[str, bool] = {}
    if adverse_driving is not None:
        updates["adverse_driving"] = adverse_driving
    if sixteen_hour_exception is not None:
        updates["sixteen_hour_exception"] = sixteen_hour_exception
    return base.model_copy(update=updates) if updates else base


def sixteen_hour_exception_applies(annotations: DayAnnotations) -> bool:
    """§ 395.1(o) — fail closed without prior-5-tours evidence and unused-this-cycle."""
    return (
        annotations.sixteen_hour_exception
        and annotations.prior_five_tours_same_location
        and not annotations.used_sixteen_hour_since_restart
    )


def resolve_federal_limits(
    annotations: DayAnnotations,
) -> Tuple[float, float, List[ViolationType]]:
    """Return (max_driving, max_duty_window, exception finding types applied).

    Adverse → 13h / 16h. § 395.1(o) → 11h / 16h. Combined → 13h / 16h.
    """
    max_driving = MAX_DRIVING_SECONDS
    max_duty = MAX_DUTY_WINDOW_SECONDS
    applied: List[ViolationType] = []

    adverse = annotations.adverse_driving
    sixteen = sixteen_hour_exception_applies(annotations)

    if adverse:
        max_driving = ADVERSE_MAX_DRIVING_SECONDS
        max_duty = ADVERSE_MAX_DUTY_WINDOW_SECONDS
        applied.append(ViolationType.ADVERSE_DRIVING_USED)
    if sixteen:
        max_duty = max(max_duty, SIXTEEN_HOUR_MAX_DUTY_WINDOW_SECONDS)
        applied.append(ViolationType.SIXTEEN_HOUR_EXCEPTION)

    return max_driving, max_duty, applied


def exception_usage_findings(
    applied: Sequence[ViolationType],
    *,
    now: datetime,
) -> List[Violation]:
    """Emit review notices when adverse / 16h limits were applied."""
    findings: List[Violation] = []
    for vtype in applied:
        if vtype == ViolationType.ADVERSE_DRIVING_USED:
            findings.append(
                Violation(
                    violation_type=vtype,
                    severity=ViolationSeverity.WARNING,
                    rule_ref="§ 395.1(b)",
                    description=(
                        "Adverse driving conditions exception applied for this day: "
                        "limits evaluated at 13h driving / 16h window. "
                        "Retain for compliance review."
                    ),
                    detected_at=now,
                )
            )
        elif vtype == ViolationType.SIXTEEN_HOUR_EXCEPTION:
            findings.append(
                Violation(
                    violation_type=vtype,
                    severity=ViolationSeverity.WARNING,
                    rule_ref="§ 395.1(o)",
                    description=(
                        "16-hour short-haul exception applied for this day: "
                        "duty window extended 14h→16h (11h driving still applies). "
                        "One use per cycle after a 34h restart."
                    ),
                    detected_at=now,
                )
            )
    return findings


def _segment_durations(
    timeline: DriverTimeline,
    as_of: datetime,
) -> List[Tuple[str, datetime, datetime, float]]:
    """Closed status segments (status, start, end, duration_seconds) up to as_of."""
    events = sorted(timeline.events, key=lambda e: e.timestamp)
    if not events:
        return []
    segments: List[Tuple[str, datetime, datetime, float]] = []
    for i, event in enumerate(events):
        start = event.timestamp
        if start > as_of:
            break
        if i + 1 < len(events):
            end = min(events[i + 1].timestamp, as_of)
        else:
            end = as_of
        if end <= start:
            continue
        segments.append((event.status, start, end, (end - start).total_seconds()))
    return segments


def _pc_duration_seconds(timeline: DriverTimeline, as_of: datetime) -> float:
    return sum(
        dur
        for status, _s, _e, dur in _segment_durations(timeline, as_of)
        if status == CanonicalDutyStatus.PERSONAL_CONVEYANCE.value
    )


def _ym_segments(
    timeline: DriverTimeline, as_of: datetime
) -> List[Tuple[datetime, datetime]]:
    return [
        (start, end)
        for status, start, end, _dur in _segment_durations(timeline, as_of)
        if status == CanonicalDutyStatus.YARD_MOVE.value
    ]


def _pc_segments(
    timeline: DriverTimeline, as_of: datetime
) -> List[Tuple[datetime, datetime]]:
    return [
        (start, end)
        for status, start, end, _dur in _segment_durations(timeline, as_of)
        if status == CanonicalDutyStatus.PERSONAL_CONVEYANCE.value
    ]


def _fixes_in_window(
    gps_fixes: Sequence[GpsFix],
    start: datetime,
    end: datetime,
) -> List[GpsFix]:
    return [f for f in gps_fixes if start <= f.timestamp <= end]


def _distance_change_toward(
    origin: WorkReportingLocation,
    fixes: Sequence[GpsFix],
) -> float:
    """Positive when the last fix is closer to origin than the first (air miles)."""
    if len(fixes) < 2:
        return 0.0
    first = fixes[0]
    last = fixes[-1]
    d0 = haversine_air_miles(
        first.latitude, first.longitude, origin.latitude, origin.longitude
    )
    d1 = haversine_air_miles(
        last.latitude, last.longitude, origin.latitude, origin.longitude
    )
    return d0 - d1


def _hours_exhaust_at(
    timeline: DriverTimeline,
    state: StateMachineResult,
    as_of: datetime,
) -> Optional[datetime]:
    """Earliest time standard 11h driving or 14h window was exhausted (if any)."""
    duty_start = state.duty_window_start
    driven = 0.0
    drive_exhaust: Optional[datetime] = None
    for status, start, _end, dur in _segment_durations(timeline, as_of):
        if status != CanonicalDutyStatus.DRIVING.value:
            continue
        if driven < MAX_DRIVING_SECONDS <= driven + dur:
            drive_exhaust = start + timedelta(seconds=MAX_DRIVING_SECONDS - driven)
            break
        driven += dur

    window_exhaust: Optional[datetime] = None
    if duty_start is not None:
        candidate = duty_start + timedelta(seconds=MAX_DUTY_WINDOW_SECONDS)
        if candidate <= as_of:
            window_exhaust = candidate

    candidates = [t for t in (drive_exhaust, window_exhaust) if t is not None]
    return min(candidates) if candidates else None


def evaluate_pc_abuse_findings(
    *,
    timeline: DriverTimeline,
    state: StateMachineResult,
    gps_fixes: Sequence[GpsFix],
    annotations: DayAnnotations,
    now: datetime,
) -> List[Violation]:
    """PC >3h; PC after hours exhaust; PC moving materially closer to next load."""
    findings: List[Violation] = []
    pc_seconds = _pc_duration_seconds(timeline, now)

    if pc_seconds > PC_ABUSE_DURATION_SECONDS:
        findings.append(
            Violation(
                violation_type=ViolationType.PC_ABUSE,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.8 / PC guidance",
                description=(
                    f"Personal conveyance used {pc_seconds / 3600:.2f}h "
                    f"(heuristic threshold: 3h). Review for PC abuse."
                ),
                detected_at=now,
                overage_seconds=pc_seconds - PC_ABUSE_DURATION_SECONDS,
            )
        )

    exhaust_at = _hours_exhaust_at(timeline, state, now)
    if exhaust_at is not None:
        for start, _end in _pc_segments(timeline, now):
            if start >= exhaust_at - timedelta(seconds=1):
                findings.append(
                    Violation(
                        violation_type=ViolationType.PC_ABUSE,
                        severity=ViolationSeverity.WARNING,
                        rule_ref="§ 395.8 / PC guidance",
                        description=(
                            "Personal conveyance used after driving or duty-window "
                            "hours were exhausted. Review for PC abuse."
                        ),
                        detected_at=now,
                    )
                )
                break

    if annotations.next_load_location is not None:
        for start, end in _pc_segments(timeline, now):
            fixes = _fixes_in_window(gps_fixes, start, end)
            closer = _distance_change_toward(annotations.next_load_location, fixes)
            if closer >= PC_TOWARD_LOAD_AIR_MILES:
                findings.append(
                    Violation(
                        violation_type=ViolationType.PC_ABUSE,
                        severity=ViolationSeverity.WARNING,
                        rule_ref="§ 395.8 / PC guidance",
                        description=(
                            f"Personal conveyance moved the vehicle ~{closer:.1f} air-miles "
                            "closer to the next load location. Review for PC abuse."
                        ),
                        detected_at=now,
                    )
                )
                break

    return findings


def evaluate_ym_abuse_findings(
    *,
    timeline: DriverTimeline,
    gps_fixes: Sequence[GpsFix],
    now: datetime,
) -> List[Violation]:
    """YM at highway speeds (>32 km/h / 20 mph) from GPS when available."""
    findings: List[Violation] = []
    for start, end in _ym_segments(timeline, now):
        fixes = _fixes_in_window(gps_fixes, start, end)
        speeds = [f.speed_kmh for f in fixes if f.speed_kmh is not None]
        if not speeds:
            # Derive speed from consecutive fixes when provider omitted speed_kmh
            for a, b in zip(fixes, fixes[1:]):
                dt = (b.timestamp - a.timestamp).total_seconds()
                if dt <= 0:
                    continue
                dist_nmi = haversine_air_miles(
                    a.latitude, a.longitude, b.latitude, b.longitude
                )
                # 1 air-mile/h ≈ 1.852 km/h
                speed = (dist_nmi / dt) * 3600.0 * 1.852
                speeds.append(speed)
        max_speed = max(speeds) if speeds else 0.0
        if max_speed > YM_HIGHWAY_SPEED_KMH:
            findings.append(
                Violation(
                    violation_type=ViolationType.YM_ABUSE,
                    severity=ViolationSeverity.WARNING,
                    rule_ref="§ 395.8 / YM guidance",
                    description=(
                        f"Yard move segment with GPS speed {max_speed:.1f} km/h "
                        f"(threshold: {YM_HIGHWAY_SPEED_KMH:.0f} km/h / 20 mph). "
                        "Review for YM falsification."
                    ),
                    detected_at=now,
                    overage_seconds=0.0,
                )
            )
            break
    return findings


def evaluate_form_and_manner_findings(
    annotations: DayAnnotations,
    *,
    now: datetime,
) -> List[Violation]:
    """§ 395.8 form & manner checks from supplied day evidence."""
    findings: List[Violation] = []

    if annotations.daily_certified is False:
        findings.append(
            Violation(
                violation_type=ViolationType.FORM_AND_MANNER_MISSING_CERT,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.8",
                description="Missing daily RODS/ELD certification for this day.",
                detected_at=now,
            )
        )

    if annotations.missing_required_fields:
        fields = ", ".join(annotations.missing_required_fields)
        findings.append(
            Violation(
                violation_type=ViolationType.FORM_AND_MANNER_MISSING_FIELDS,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.8",
                description=f"Missing required log fields: {fields}.",
                detected_at=now,
            )
        )

    if annotations.unassigned_driving_seconds > 0:
        findings.append(
            Violation(
                violation_type=ViolationType.FORM_AND_MANNER_UNASSIGNED_DRIVING,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.8",
                description=(
                    f"Unassigned driving time detected "
                    f"({annotations.unassigned_driving_seconds / 60:.0f} min). "
                    "Assign or annotate before certification."
                ),
                detected_at=now,
                overage_seconds=annotations.unassigned_driving_seconds,
            )
        )

    for edit in annotations.log_edits:
        if (
            edit.from_status == CanonicalDutyStatus.DRIVING.value
            and edit.to_status in _DRIVING_TO_REST_EDIT_TARGETS
        ):
            findings.append(
                Violation(
                    violation_type=ViolationType.FORM_AND_MANNER_LOG_EDIT,
                    severity=ViolationSeverity.WARNING,
                    rule_ref="§ 395.8",
                    description=(
                        f"Log edit {edit.from_status}→{edit.to_status} on "
                        f"{edit.field_changed} at {edit.edited_at.isoformat()}. "
                        "Review driving-time edits."
                    ),
                    detected_at=now,
                )
            )
            break  # one finding per evaluation is enough for the risk channel

    if annotations.eld_malfunction_days > ELD_MALFUNCTION_PAPER_DAYS:
        findings.append(
            Violation(
                violation_type=ViolationType.FORM_AND_MANNER_ELD_MALFUNCTION,
                severity=ViolationSeverity.VIOLATION,
                rule_ref="§ 395.34 / § 395.8",
                description=(
                    f"ELD malfunction/data diagnostic active for "
                    f"{annotations.eld_malfunction_days} days "
                    f"(paper RODS required after {ELD_MALFUNCTION_PAPER_DAYS} days)."
                ),
                detected_at=now,
                overage_seconds=float(
                    (annotations.eld_malfunction_days - ELD_MALFUNCTION_PAPER_DAYS)
                    * 86400
                ),
            )
        )

    return findings


def evaluate_findings(
    *,
    timeline: DriverTimeline,
    state: StateMachineResult,
    annotations: DayAnnotations,
    gps_fixes: Optional[Sequence[GpsFix]] = None,
    now: datetime,
    include_federal_exceptions: bool = True,
) -> List[Violation]:
    """Run Phase 6 finding evaluators and return audit-only risk findings.

    When ``include_federal_exceptions`` is True, emit adverse/16h usage notices
    if those limits were applied (caller still adjusts clock limits separately).
    """
    fixes: Sequence[GpsFix] = gps_fixes or ()
    findings: List[Violation] = []

    if include_federal_exceptions:
        _drive, _duty, applied = resolve_federal_limits(annotations)
        findings.extend(exception_usage_findings(applied, now=now))

    findings.extend(
        evaluate_pc_abuse_findings(
            timeline=timeline,
            state=state,
            gps_fixes=fixes,
            annotations=annotations,
            now=now,
        )
    )
    findings.extend(
        evaluate_ym_abuse_findings(timeline=timeline, gps_fixes=fixes, now=now)
    )
    findings.extend(evaluate_form_and_manner_findings(annotations, now=now))

    logger.debug(
        "Findings driver=%s count=%d types=%s",
        timeline.driver_id,
        len(findings),
        [f.violation_type.value for f in findings],
    )
    return findings
