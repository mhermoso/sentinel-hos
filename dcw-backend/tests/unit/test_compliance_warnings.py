"""WARNING entries must not flip ComplianceResult.is_compliant to False."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import (
    ComplianceResult,
    DriverTimeline,
    Violation,
    ViolationSeverity,
    ViolationType,
)
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = timezone.utc


def _ts(hours: float) -> datetime:
    return datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _short_drive_timeline() -> DriverTimeline:
    """10h off-duty, 2h driving, then off — no daily limit pressure."""
    events = [
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(0),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(10),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(12),
        ),
    ]
    return DriverTimeline(driver_id="drv-warn", tenant_id="tenant1", events=events)


def _driving_with_mid_break_timeline() -> DriverTimeline:
    """Qualify rest, drive 7h, take 30m break, resume driving (open)."""
    events = [
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(0),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(10),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(17),  # 7h driving
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(17.5),  # 30m break complete
        ),
    ]
    return DriverTimeline(driver_id="drv-warn-drive", tenant_id="tenant1", events=events)


def test_is_compliant_true_when_only_warnings() -> None:
    now = _ts(20.5)
    result = ComplianceResult(
        driver_id="d1",
        tenant_id="t1",
        evaluated_at=now,
        rule_pack_version="fmcsa-us-property@1.3.0",
        inputs_hash="abc",
        driving_remaining_seconds=1800.0,
        duty_window_remaining_seconds=7200.0,
        break_required=False,
        weekly_hours_used=40.0,
        weekly_hours_remaining=30.0,
        violations=[
            Violation(
                violation_type=ViolationType.DRIVING_LIMIT,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.3(a)(3)(i)",
                description="30 min driving remaining",
                detected_at=now,
            )
        ],
    )
    assert result.is_compliant is True
    assert result.highest_severity == ViolationSeverity.WARNING


def test_is_compliant_false_on_violation_even_with_warnings() -> None:
    now = _ts(21)
    result = ComplianceResult(
        driver_id="d1",
        tenant_id="t1",
        evaluated_at=now,
        rule_pack_version="fmcsa-us-property@1.3.0",
        inputs_hash="abc",
        driving_remaining_seconds=0.0,
        duty_window_remaining_seconds=3600.0,
        break_required=False,
        weekly_hours_used=40.0,
        weekly_hours_remaining=30.0,
        violations=[
            Violation(
                violation_type=ViolationType.DRIVING_LIMIT,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.3(a)(3)(i)",
                description="approaching",
                detected_at=now,
            ),
            Violation(
                violation_type=ViolationType.DUTY_WINDOW,
                severity=ViolationSeverity.VIOLATION,
                rule_ref="§ 395.3(a)(2)",
                description="exceeded",
                detected_at=now,
                overage_seconds=60.0,
            ),
        ],
    )
    assert result.is_compliant is False
    assert result.highest_severity == ViolationSeverity.VIOLATION


def test_rule_pack_weekly_warning_remains_compliant() -> None:
    """68h weekly used → 2h remaining → WARNING only; still compliant."""
    pack = RulePack()
    timeline = _short_drive_timeline()
    result = pack.evaluate(
        timeline,
        inputs_hash="hash-weekly-warn",
        weekly_duty_seconds=68.0 * 3600.0,
        as_of=_ts(12),
    )

    assert any(
        v.severity == ViolationSeverity.WARNING
        and v.violation_type == ViolationType.WEEKLY_CYCLE
        for v in result.violations
    )
    assert not any(
        v.severity in (ViolationSeverity.VIOLATION, ViolationSeverity.CRITICAL)
        for v in result.violations
    )
    assert result.is_compliant is True


def test_rule_pack_driving_limit_warning_remains_compliant() -> None:
    """10.5h cumulative driving with mid-shift break → 30m left → WARNING."""
    pack = RulePack()
    timeline = _driving_with_mid_break_timeline()
    # 7h + 3.5h = 10.5h driving at as_of=_ts(21)
    result = pack.evaluate(
        timeline,
        inputs_hash="hash-drive-warn",
        weekly_duty_seconds=10.5 * 3600.0,
        as_of=_ts(21),
    )

    assert result.driving_remaining_seconds == pytest.approx(1800.0)
    assert any(
        v.severity == ViolationSeverity.WARNING
        and v.violation_type == ViolationType.DRIVING_LIMIT
        for v in result.violations
    )
    assert not any(
        v.severity in (ViolationSeverity.VIOLATION, ViolationSeverity.CRITICAL)
        for v in result.violations
    )
    assert result.is_compliant is True


def test_rule_pack_eleven_hour_exceeded_is_non_compliant() -> None:
    pack = RulePack()
    timeline = _driving_with_mid_break_timeline()
    # 7h + 4h = 11h driving at as_of=_ts(21.5)
    result = pack.evaluate(
        timeline,
        inputs_hash="hash-viol",
        weekly_duty_seconds=11.0 * 3600.0,
        as_of=_ts(21.5),
    )

    assert any(
        v.severity == ViolationSeverity.VIOLATION
        and v.violation_type == ViolationType.DRIVING_LIMIT
        for v in result.violations
    )
    assert result.is_compliant is False
