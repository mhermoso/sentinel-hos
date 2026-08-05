"""Shared pack-module protocol and unsupported-result helper."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.domains.engine.schemas import (
    ComplianceResult,
    DayAnnotations,
    DriverProfile,
    DriverTimeline,
    GpsFix,
    RulesetId,
    RulesetStatus,
    Violation,
    ViolationSeverity,
    ViolationType,
)


class RulePackModule(Protocol):
    """Thin pack interface selected by the ruleset router."""

    pack_id: str
    ruleset: RulesetId
    implemented: bool

    def evaluate(
        self,
        timeline: DriverTimeline,
        inputs_hash: str,
        *,
        version: str,
        weekly_duty_seconds: float,
        as_of: datetime | None,
        profile: DriverProfile,
        gps_fixes: Sequence[GpsFix] | None = None,
        short_haul_failure_days_30: int = 0,
        day_annotations: DayAnnotations | None = None,
        adverse_driving: bool | None = None,
        sixteen_hour_exception: bool | None = None,
    ) -> ComplianceResult: ...


def build_unsupported_result(
    *,
    timeline: DriverTimeline,
    inputs_hash: str,
    version: str,
    evaluated_at: datetime,
    ruleset: RulesetId,
    pack_id: str,
) -> ComplianceResult:
    """Return empty clocks + RULESET_UNSUPPORTED (no federal/TX clocks applied)."""
    finding = Violation(
        violation_type=ViolationType.RULESET_UNSUPPORTED,
        severity=ViolationSeverity.WARNING,
        rule_ref="DCW ruleset router",
        description=(
            f"Ruleset {ruleset.value} ({pack_id}) is not yet implemented; "
            "HOS clocks for this regime were not evaluated."
        ),
        detected_at=evaluated_at,
        overage_seconds=0.0,
    )
    return ComplianceResult(
        driver_id=timeline.driver_id,
        tenant_id=timeline.tenant_id,
        evaluated_at=evaluated_at,
        rule_pack_version=version,
        inputs_hash=inputs_hash,
        driving_remaining_seconds=0.0,
        duty_window_remaining_seconds=0.0,
        break_required=False,
        weekly_hours_used=0.0,
        weekly_hours_remaining=0.0,
        violations=[finding],
        selected_ruleset=ruleset,
        ruleset_status=RulesetStatus.NOT_IMPLEMENTED,
        ruleset_pack_id=pack_id,
    )
