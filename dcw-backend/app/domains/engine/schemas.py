"""Pydantic schemas for the DCW Deterministic Compliance Engine.

Defines violations, compliance state, driver profiles, and timeline types
consumed by the ruleset router and 49 CFR Part 395 state machine.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ViolationType(str, Enum):
    """HOS violation / finding categories (federal + Texas packs)."""

    DRIVING_LIMIT = "DRIVING_LIMIT"          # § 395.3(a)(3)(i) — 11h
    DUTY_WINDOW = "DUTY_WINDOW"              # § 395.3(a)(2) — 14h
    REST_BREAK = "REST_BREAK"               # § 395.3(a)(3)(ii) — 30 min
    WEEKLY_CYCLE = "WEEKLY_CYCLE"           # § 395.3(b) — 60/70h
    RESTART_INVALID = "RESTART_INVALID"     # legacy only — not emitted since @2.5.0
    RULESET_UNSUPPORTED = "RULESET_UNSUPPORTED"  # pack not yet implemented
    # Short-haul / RODS (Rulesets B/D)
    EXEMPTION_LOST = "EXEMPTION_LOST"        # short-haul conditions failed
    RODS_REQUIRED = "RODS_REQUIRED"          # RODS required after exemption loss
    ELD_REQUIRED_8_IN_30 = "ELD_REQUIRED_8_IN_30"  # 8+ short-haul fail days in 30
    # Texas intrastate (Ruleset C/D) — 37 TAC §4.12
    TX_DRIVING_LIMIT = "TX_DRIVING_LIMIT"    # 12h after 8h off
    TX_DUTY_LIMIT = "TX_DUTY_LIMIT"          # 15h accumulated ON+D
    # Phase 6 — exceptions + form & manner / abuse risk findings
    ADVERSE_DRIVING_USED = "ADVERSE_DRIVING_USED"  # § 395.1(b) day flag applied
    SIXTEEN_HOUR_EXCEPTION = "SIXTEEN_HOUR_EXCEPTION"  # § 395.1(o) day exception
    PC_ABUSE = "PC_ABUSE"                    # personal conveyance abuse heuristics
    YM_ABUSE = "YM_ABUSE"                    # yard-move falsification heuristics
    FORM_AND_MANNER_MISSING_CERT = "FORM_AND_MANNER_MISSING_CERT"
    FORM_AND_MANNER_MISSING_FIELDS = "FORM_AND_MANNER_MISSING_FIELDS"
    FORM_AND_MANNER_UNASSIGNED_DRIVING = "FORM_AND_MANNER_UNASSIGNED_DRIVING"
    FORM_AND_MANNER_LOG_EDIT = "FORM_AND_MANNER_LOG_EDIT"
    FORM_AND_MANNER_ELD_MALFUNCTION = "FORM_AND_MANNER_ELD_MALFUNCTION"


# Exception-used notices: persisted for review but do not flip compliance.
COMPLIANCE_NEUTRAL_FINDINGS = frozenset(
    {
        ViolationType.ADVERSE_DRIVING_USED,
        ViolationType.SIXTEEN_HOUR_EXCEPTION,
    }
)

# Risk / form findings — audit + dashboard; sweeper skips Twilio by default.
NON_TELEPHONY_FINDINGS = frozenset(
    {
        ViolationType.RULESET_UNSUPPORTED,
        ViolationType.ADVERSE_DRIVING_USED,
        ViolationType.SIXTEEN_HOUR_EXCEPTION,
        ViolationType.PC_ABUSE,
        ViolationType.YM_ABUSE,
        ViolationType.FORM_AND_MANNER_MISSING_CERT,
        ViolationType.FORM_AND_MANNER_MISSING_FIELDS,
        ViolationType.FORM_AND_MANNER_UNASSIGNED_DRIVING,
        ViolationType.FORM_AND_MANNER_LOG_EDIT,
        ViolationType.FORM_AND_MANNER_ELD_MALFUNCTION,
    }
)


class ViolationSeverity(str, Enum):
    """Alert severity tier driving Twilio call vs. SMS dispatch."""

    WARNING = "WARNING"      # Approaching limit (e.g. 30 min remaining)
    VIOLATION = "VIOLATION"  # Limit reached
    CRITICAL = "CRITICAL"    # Actively exceeded limit


class OperatingAuthority(str, Enum):
    """Driver operating authority used for base ruleset selection (PDF §2)."""

    INTERSTATE = "INTERSTATE"
    TX_INTRASTATE = "TX_INTRASTATE"


class HosCycle(str, Enum):
    """Weekly cycle configuration for the driver."""

    CYCLE_60_7 = "60_7"
    CYCLE_70_8 = "70_8"
    CYCLE_TX_70_7 = "TX_70_7"

    @classmethod
    def parse(cls, value: str | HosCycle) -> HosCycle:
        """Parse cycle strings including legacy aliases (``60/7``, ``70/8``)."""
        if isinstance(value, cls):
            return value
        normalized = value.strip().upper().replace("/", "_").replace("-", "_")
        aliases = {
            "60_7": cls.CYCLE_60_7,
            "70_8": cls.CYCLE_70_8,
            "TX_70_7": cls.CYCLE_TX_70_7,
            "TX70_7": cls.CYCLE_TX_70_7,
        }
        if normalized in aliases:
            return aliases[normalized]
        return cls(normalized)


class RulesetId(str, Enum):
    """PDF §2 ruleset identifiers selected by the daily router."""

    A = "A"  # Federal interstate property-carrying
    B = "B"  # Federal 150 air-mile short-haul
    C = "C"  # Texas intrastate
    D = "D"  # Texas short-haul


class RulesetStatus(str, Enum):
    """Whether the selected pack module can evaluate clocks."""

    IMPLEMENTED = "IMPLEMENTED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


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


class WorkReportingLocation(BaseModel):
    """Work-reporting location for short-haul air-mile checks (Phase 4)."""

    model_config = ConfigDict(frozen=True)

    latitude: float
    longitude: float


class GpsFix(BaseModel):
    """Mapped GPS point for short-haul / YM heuristics (engine evaluation input).

    Loaded from ``gps_breadcrumbs`` at the repository/sweeper boundary and
    passed into pack evaluation — packs do not query the GPS store (ADR-007).
    """

    model_config = ConfigDict(frozen=True)

    latitude: float
    longitude: float
    timestamp: datetime
    speed_kmh: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Vehicle speed in km/h when reported (YM highway heuristic)",
    )


class LogEditEvidence(BaseModel):
    """One log edit considered for form & manner review (§ 395.8)."""

    model_config = ConfigDict(frozen=True)

    from_status: str
    to_status: str
    edited_at: datetime
    field_changed: str = "status"


class DayAnnotations(BaseModel):
    """Per home-terminal-day exception flags and form & manner evidence.

    Passed into ``RulePack.evaluate`` (tests / future day-annotation store).
    Repository currently returns an empty stub until persistence ships.
    """

    model_config = ConfigDict(frozen=True)

    # Exceptions (false-positive suppression / extended limits)
    adverse_driving: bool = False
    sixteen_hour_exception: bool = False
    # § 395.1(o) eligibility evidence (fail closed unless both satisfied)
    prior_five_tours_same_location: bool = False
    used_sixteen_hour_since_restart: bool = False

    # Form & manner evidence (§ 395.8) — None means "not supplied / unknown"
    daily_certified: Optional[bool] = None
    missing_required_fields: List[str] = Field(default_factory=list)
    unassigned_driving_seconds: float = Field(0.0, ge=0.0)
    log_edits: List[LogEditEvidence] = Field(default_factory=list)
    eld_malfunction_days: int = Field(0, ge=0)

    # Optional next-load lat/lon for PC "toward load" heuristic
    next_load_location: Optional[WorkReportingLocation] = None


class DriverProfile(BaseModel):
    """Per-driver, tenant-scoped configuration for ruleset selection."""

    model_config = ConfigDict(frozen=True)

    driver_id: str
    tenant_id: str
    operating_authority: OperatingAuthority = OperatingAuthority.INTERSTATE
    short_haul_eligible: bool = False
    cdl_required: bool = True
    cycle: HosCycle = HosCycle.CYCLE_70_8
    home_terminal_timezone: str = Field(
        ...,
        description="IANA timezone for home-terminal day boundaries / restart checks",
    )
    work_reporting_location: Optional[WorkReportingLocation] = None
    vehicle_weight_class: Optional[str] = None
    hazmat_placard: Optional[bool] = None

    @field_validator("cycle", mode="before")
    @classmethod
    def _parse_cycle(cls, value: object) -> HosCycle:
        if isinstance(value, HosCycle):
            return value
        if isinstance(value, str):
            return HosCycle.parse(value)
        raise TypeError(f"Unsupported cycle value: {value!r}")


def default_driver_profile(
    *,
    driver_id: str,
    tenant_id: str,
    home_terminal_timezone: str | None = None,
) -> DriverProfile:
    """Defaults preserving current interstate 70/8 behavior for fleets without profiles."""
    from app.core.config import settings

    return DriverProfile(
        driver_id=driver_id,
        tenant_id=tenant_id,
        operating_authority=OperatingAuthority.INTERSTATE,
        short_haul_eligible=False,
        cdl_required=True,
        cycle=HosCycle.CYCLE_70_8,
        home_terminal_timezone=(
            home_terminal_timezone or settings.DEFAULT_HOME_TERMINAL_TIMEZONE
        ),
        work_reporting_location=None,
        vehicle_weight_class=None,
        hazmat_placard=None,
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

    # ── Ruleset router metadata (Phase 3+) ──────────────────────────────
    selected_ruleset: Optional[RulesetId] = Field(
        default=None,
        description="Ruleset A/B/C/D selected by the daily router",
    )
    ruleset_status: Optional[RulesetStatus] = Field(
        default=None,
        description="IMPLEMENTED when pack evaluates clocks; NOT_IMPLEMENTED for stubs",
    )
    ruleset_pack_id: Optional[str] = Field(
        default=None,
        description="Pack module id, e.g. fmcsa_us_property",
    )

    @property
    def is_compliant(self) -> bool:
        """True when no compliance-affecting violations are present.

        Exception-used notices (adverse / 16h) are persisted for review but
        do not flip compliance by themselves.
        """
        return not any(
            v.violation_type not in COMPLIANCE_NEUTRAL_FINDINGS for v in self.violations
        )

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
