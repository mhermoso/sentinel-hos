"""Phase 6: adverse/16h exceptions, PC/YM abuse, form & manner findings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.engine.findings import (
    ADVERSE_MAX_DRIVING_SECONDS,
    ADVERSE_MAX_DUTY_WINDOW_SECONDS,
    SIXTEEN_HOUR_MAX_DUTY_WINDOW_SECONDS,
    resolve_federal_limits,
)
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import (
    NON_TELEPHONY_FINDINGS,
    DayAnnotations,
    GpsFix,
    LogEditEvidence,
    ViolationSeverity,
    ViolationType,
    WorkReportingLocation,
    DriverTimeline,
)
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = timezone.utc


def _ts(hours: float) -> datetime:
    return datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _evt(status: CanonicalDutyStatus, hours: float) -> DriverTimeline.HOSEvent:
    return DriverTimeline.HOSEvent(status=status.value, timestamp=_ts(hours))


def _timeline(*events: DriverTimeline.HOSEvent) -> DriverTimeline:
    return DriverTimeline(driver_id="d1", tenant_id="t1", events=list(events))


def _shift_drive(hours_off: float, drive_hours: float) -> DriverTimeline:
    """Qualifying rest then continuous driving for ``drive_hours``."""
    return _timeline(
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, hours_off),
    )


# ── Adverse / 16h limit extension ─────────────────────────────────────────


def test_adverse_driving_extends_to_13h_16h() -> None:
    annotations = DayAnnotations(adverse_driving=True)
    max_d, max_w, applied = resolve_federal_limits(annotations)
    assert max_d == ADVERSE_MAX_DRIVING_SECONDS
    assert max_w == ADVERSE_MAX_DUTY_WINDOW_SECONDS
    assert ViolationType.ADVERSE_DRIVING_USED in applied

    # Include a 30-min non-driving break so REST_BREAK does not fire.
    with_break = _timeline(
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
        _evt(CanonicalDutyStatus.ON_DUTY, 17),  # 7h drive
        _evt(CanonicalDutyStatus.DRIVING, 17.5),  # 30-min break reset
    )

    pack = RulePack()
    base = pack.evaluate(with_break, inputs_hash="h", as_of=_ts(10 + 12))
    assert any(
        v.violation_type == ViolationType.DRIVING_LIMIT
        and v.severity in (ViolationSeverity.VIOLATION, ViolationSeverity.CRITICAL)
        for v in base.violations
    )

    # ~11.5h total driving under adverse 13h (>60 min remaining, no WARNING)
    adverse = pack.evaluate(
        with_break,
        inputs_hash="h",
        as_of=_ts(10 + 12),  # 7h + 4.5h after break = 11.5h drive; window 12h
        adverse_driving=True,
    )
    drive_limit = [
        v
        for v in adverse.violations
        if v.violation_type == ViolationType.DRIVING_LIMIT
    ]
    assert drive_limit == []
    assert any(v.violation_type == ViolationType.ADVERSE_DRIVING_USED for v in adverse.violations)
    assert adverse.is_compliant  # exception notice is compliance-neutral
    assert adverse.driving_remaining_seconds == pytest.approx(1.5 * 3600.0)


def test_sixteen_hour_exception_extends_window_keeps_11h_drive() -> None:
    annotations = DayAnnotations(
        sixteen_hour_exception=True,
        prior_five_tours_same_location=True,
        used_sixteen_hour_since_restart=False,
    )
    max_d, max_w, applied = resolve_federal_limits(annotations)
    assert max_d == 11 * 3600.0
    assert max_w == SIXTEEN_HOUR_MAX_DUTY_WINDOW_SECONDS
    assert ViolationType.SIXTEEN_HOUR_EXCEPTION in applied

    pack = RulePack()
    # 15h wall-clock while driving — violation under 14h, not under 16h
    result = pack.evaluate(
        _shift_drive(10, 0),
        inputs_hash="h",
        as_of=_ts(10 + 15),
        day_annotations=annotations,
    )
    duty_hard = [
        v
        for v in result.violations
        if v.violation_type == ViolationType.DUTY_WINDOW
        and v.severity != ViolationSeverity.WARNING
    ]
    assert duty_hard == []
    assert any(v.violation_type == ViolationType.SIXTEEN_HOUR_EXCEPTION for v in result.violations)

    # 12h driving still violates 11h under § 395.1(o)
    drive12 = pack.evaluate(
        _shift_drive(10, 0),
        inputs_hash="h",
        as_of=_ts(10 + 12),
        day_annotations=annotations,
    )
    assert any(
        v.violation_type == ViolationType.DRIVING_LIMIT
        and v.severity in (ViolationSeverity.VIOLATION, ViolationSeverity.CRITICAL)
        for v in drive12.violations
    )


def test_sixteen_hour_fail_closed_without_prior_five() -> None:
    annotations = DayAnnotations(
        sixteen_hour_exception=True,
        prior_five_tours_same_location=False,
    )
    _d, max_w, applied = resolve_federal_limits(annotations)
    assert max_w == 14 * 3600.0
    assert applied == []


# ── PC / YM abuse ─────────────────────────────────────────────────────────


def test_pc_abuse_over_three_hours() -> None:
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.ON_DUTY, 10),
        _evt(CanonicalDutyStatus.PERSONAL_CONVEYANCE, 11),
        _evt(CanonicalDutyStatus.OFF_DUTY, 15),  # 4h PC
    ]
    result = RulePack().evaluate(_timeline(*events), inputs_hash="h", as_of=_ts(15))
    assert any(v.violation_type == ViolationType.PC_ABUSE for v in result.violations)


def test_pc_abuse_after_hours_exhaust() -> None:
    # Drive 11h then PC
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
        _evt(CanonicalDutyStatus.PERSONAL_CONVEYANCE, 21),  # after 11h driving
        _evt(CanonicalDutyStatus.OFF_DUTY, 21.5),
    ]
    result = RulePack().evaluate(_timeline(*events), inputs_hash="h", as_of=_ts(21.5))
    assert any(
        v.violation_type == ViolationType.PC_ABUSE
        and "exhausted" in v.description.lower()
        for v in result.violations
    )


def test_pc_abuse_toward_next_load() -> None:
    load = WorkReportingLocation(latitude=32.0, longitude=-97.0)
    # PC moves from far to near load (~3+ air-miles closer)
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.ON_DUTY, 10),
        _evt(CanonicalDutyStatus.PERSONAL_CONVEYANCE, 11),
        _evt(CanonicalDutyStatus.OFF_DUTY, 12),
    ]
    gps = [
        GpsFix(latitude=32.1, longitude=-97.0, timestamp=_ts(11.1)),
        GpsFix(latitude=32.02, longitude=-97.0, timestamp=_ts(11.8)),
    ]
    result = RulePack().evaluate(
        _timeline(*events),
        inputs_hash="h",
        as_of=_ts(12),
        gps_fixes=gps,
        day_annotations=DayAnnotations(next_load_location=load),
    )
    assert any(
        v.violation_type == ViolationType.PC_ABUSE and "next load" in v.description.lower()
        for v in result.violations
    )


def test_ym_abuse_highway_speed() -> None:
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.YARD_MOVE, 10),
        _evt(CanonicalDutyStatus.OFF_DUTY, 10.5),
    ]
    gps = [
        GpsFix(
            latitude=32.0,
            longitude=-97.0,
            timestamp=_ts(10.1),
            speed_kmh=55.0,
        ),
    ]
    result = RulePack().evaluate(
        _timeline(*events),
        inputs_hash="h",
        as_of=_ts(10.5),
        gps_fixes=gps,
    )
    assert any(v.violation_type == ViolationType.YM_ABUSE for v in result.violations)


def test_ym_no_abuse_at_yard_speed() -> None:
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.YARD_MOVE, 10),
        _evt(CanonicalDutyStatus.OFF_DUTY, 10.5),
    ]
    gps = [
        GpsFix(
            latitude=32.0,
            longitude=-97.0,
            timestamp=_ts(10.1),
            speed_kmh=10.0,
        ),
    ]
    result = RulePack().evaluate(
        _timeline(*events),
        inputs_hash="h",
        as_of=_ts(10.5),
        gps_fixes=gps,
    )
    assert not any(v.violation_type == ViolationType.YM_ABUSE for v in result.violations)


# ── Form & manner ─────────────────────────────────────────────────────────


def test_form_missing_certification() -> None:
    result = RulePack().evaluate(
        _shift_drive(10, 0),
        inputs_hash="h",
        as_of=_ts(12),
        day_annotations=DayAnnotations(daily_certified=False),
    )
    assert any(
        v.violation_type == ViolationType.FORM_AND_MANNER_MISSING_CERT for v in result.violations
    )


def test_form_missing_fields_unassigned_edit_eld() -> None:
    annotations = DayAnnotations(
        missing_required_fields=["trailer_number", "shipping_doc"],
        unassigned_driving_seconds=1800.0,
        log_edits=[
            LogEditEvidence(
                from_status=CanonicalDutyStatus.DRIVING.value,
                to_status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
                edited_at=_ts(14),
            )
        ],
        eld_malfunction_days=9,
    )
    result = RulePack().evaluate(
        _shift_drive(10, 0),
        inputs_hash="h",
        as_of=_ts(12),
        day_annotations=annotations,
    )
    types = {v.violation_type for v in result.violations}
    assert ViolationType.FORM_AND_MANNER_MISSING_FIELDS in types
    assert ViolationType.FORM_AND_MANNER_UNASSIGNED_DRIVING in types
    assert ViolationType.FORM_AND_MANNER_LOG_EDIT in types
    assert ViolationType.FORM_AND_MANNER_ELD_MALFUNCTION in types
    eld = next(
        v for v in result.violations if v.violation_type == ViolationType.FORM_AND_MANNER_ELD_MALFUNCTION
    )
    assert eld.severity == ViolationSeverity.VIOLATION


def test_phase6_findings_are_non_telephony() -> None:
    for vtype in (
        ViolationType.ADVERSE_DRIVING_USED,
        ViolationType.SIXTEEN_HOUR_EXCEPTION,
        ViolationType.PC_ABUSE,
        ViolationType.YM_ABUSE,
        ViolationType.FORM_AND_MANNER_MISSING_CERT,
        ViolationType.FORM_AND_MANNER_MISSING_FIELDS,
        ViolationType.FORM_AND_MANNER_UNASSIGNED_DRIVING,
        ViolationType.FORM_AND_MANNER_LOG_EDIT,
        ViolationType.FORM_AND_MANNER_ELD_MALFUNCTION,
        ViolationType.RULESET_UNSUPPORTED,
    ):
        assert vtype in NON_TELEPHONY_FINDINGS


def test_pack_version_is_2_4_0() -> None:
    assert RulePack().version == "fmcsa-us-property@2.5.0"
