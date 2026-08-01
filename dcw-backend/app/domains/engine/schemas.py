"""Pydantic schemas for the DCW Deterministic Compliance Engine.

Defines violations, compliance state, and driver timeline types consumed
by the 49 CFR Part 395 state machine and all rule calculators.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ViolationType(str, Enum):
    """FMCSA 49 CFR Part 395 violation categories."""

    DRIVING_LIMIT = "DRIVING_LIMIT"          # § 395.3(a)(3)(i) — 11h
    DUTY_WINDOW = "DUTY_WINDOW"              # § 395.3(a)(2) — 14h
    REST_BREAK = "REST_BREAK"               # § 395.3(a)(3)(ii) — 30 min
    WEEKLY_CYCLE = "WEEKLY_CYCLE"           # § 395.3(b) — 60/70h
    RESTART_INVALID = "RESTART_INVALID"     # § 395.3(c) — 34h restart


class ViolationSeverity(str, Enum):
    """Alert severity tier driving Twilio call vs. SMS dispatch."""

    WARNING = "WARNING"      # Approaching limit (e.g. 30 min remaining)
    VIOLATION = "VIOLATION"  # Limit reached
    CRITICAL = "CRITICAL"    # Actively exceeded limit


class Violation(BaseModel):
    """A single detected rule violation for a driver."""

    model_config = ConfigDict(frozen=True)

    violation_type: ViolationType
    severity: ViolationSeverity
    rule_ref: str = Field(..., description="CFR reference, e.g. '§ 395.3(a)(3)(i)'")
    description: str
    detected_at: datetime
    overage_seconds: float = Field(
        0.0, ge=0.0, description="How many seconds the limit has been exceeded"
    )


class DriverTimeline(BaseModel):
    """A driver's ordered HOS event history used for compliance evaluation."""

    driver_id: str
    tenant_id: str
    events: List["HOSEvent"] = Field(default_factory=list)

    class HOSEvent(BaseModel):
        """A single HOS status event in a driver timeline."""

        status: str  # CanonicalDutyStatus value
        timestamp: datetime
        duration_seconds: float = 0.0  # computed gap to next event


DriverTimeline.model_rebuild()


class ShiftWindow(BaseModel):
    """Represents a detected shift (on-duty period after qualifying off-duty)."""

    shift_start: datetime
    qualifying_rest_before: datetime
    cumulative_driving_seconds: float = 0.0
    cumulative_duty_seconds: float = 0.0
    driving_since_break_seconds: float = 0.0  # for 30-min break tracking


class ComplianceResult(BaseModel):
    """Output of a single rule-pack evaluation for one driver."""

    model_config = ConfigDict(frozen=True)

    driver_id: str
    tenant_id: str
    evaluated_at: datetime
    rule_pack_version: str
    inputs_hash: str

    # ── Remaining time countdowns ───────────────────────────────────────
    driving_remaining_seconds: float = Field(
        ..., ge=0.0, description="Seconds of driving remaining under 11h limit"
    )
    duty_window_remaining_seconds: float = Field(
        ..., ge=0.0, description="Seconds remaining in 14h duty window"
    )
    break_required: bool = Field(
        ..., description="True if 30-min rest break is now required"
    )
    weekly_hours_used: float = Field(
        ..., ge=0.0, description="Cumulative duty hours used in rolling weekly cycle"
    )
    weekly_hours_remaining: float = Field(
        ..., ge=0.0, description="Hours remaining before 60/70h weekly limit"
    )

    # ── Violations detected ─────────────────────────────────────────────
    violations: List[Violation] = Field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        """True when no active violations are present."""
        return len(self.violations) == 0

    @property
    def highest_severity(self) -> Optional[ViolationSeverity]:
        """Return the worst severity level across all active violations."""
        if not self.violations:
            return None
        order = [ViolationSeverity.CRITICAL, ViolationSeverity.VIOLATION, ViolationSeverity.WARNING]
        for sev in order:
            if any(v.severity == sev for v in self.violations):
                return sev
        return None
