"""49 CFR Part 395 rule calculators — all 5 HOS limit rules.

Each calculator is a pure function that accepts the StateMachineResult
and returns a (possibly empty) list of Violation objects.  No I/O,
no randomness — complete determinism is guaranteed (ADR-004).

Rules implemented:
  1. 11-Hour Driving Limit        § 395.3(a)(3)(i)
  2. 14-Hour Duty Window          § 395.3(a)(2)
  3. 30-Minute Rest Break         § 395.3(a)(3)(ii)
  4. 60/70-Hour Weekly Cycle      § 395.3(b)
  5. 34-Hour Restart              § 395.3(c)

Severity (PDF §8.3 → ViolationSeverity enum names kept stable):
  ADVISORY → WARNING   within 60 min of 11h/14h/8h break; weekly used >90%
  SERIOUS  → VIOLATION limit reached or ≤15 min overage; missed break; cycle exceeded
  CRITICAL → CRITICAL  overage >15 min on driving / duty-window limits
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from app.core.config import settings
from app.domains.engine.schemas import (
    Violation,
    ViolationSeverity,
    ViolationType,
)
from app.domains.engine.state_machine import StateMachineResult

logger = logging.getLogger("dcw.engine.calculators")

# ── Limit Constants ────────────────────────────────────────────────────────

MAX_DRIVING_SECONDS: float = 11 * 3600.0          # 11h = 39 600 s
MAX_DUTY_WINDOW_SECONDS: float = 14 * 3600.0      # 14h = 50 400 s
MAX_DRIVING_BEFORE_BREAK_SECONDS: float = 8 * 3600.0  # 8h  = 28 800 s
REQUIRED_BREAK_SECONDS: float = 1800.0            # 30 min

# PDF §8.3 severity thresholds (enum names WARNING/VIOLATION/CRITICAL unchanged)
WARNING_THRESHOLD_SECONDS: float = 3600.0         # 60 min advisory window
CRITICAL_OVERAGE_SECONDS: float = 15 * 60.0       # >15 min overage → CRITICAL
WEEKLY_WARNING_USED_FRACTION: float = 0.90        # warn when used >90% of cycle


def _severity_for_limit_overage(overage_seconds: float) -> ViolationSeverity:
    """PDF §8.3: ≤15 min overage → VIOLATION (SERIOUS); >15 min → CRITICAL."""
    if overage_seconds > CRITICAL_OVERAGE_SECONDS:
        return ViolationSeverity.CRITICAL
    return ViolationSeverity.VIOLATION


# ── 1. 11-Hour Driving Limit ─────────────────────────────────────────────

def check_driving_limit(
    state: StateMachineResult,
    now: datetime,
    *,
    max_driving_seconds: float = MAX_DRIVING_SECONDS,
) -> tuple[float, List[Violation]]:
    """§ 395.3(a)(3)(i) — Maximum driving after 10h off-duty (default 11h).

    ``max_driving_seconds`` may be raised for adverse driving (§ 395.1(b) → 13h).

    Returns:
        (driving_remaining_seconds, violations)
    """
    driven = state.current_shift.cumulative_driving_seconds if state.current_shift else 0.0
    limit_h = max_driving_seconds / 3600.0
    remaining = max(0.0, max_driving_seconds - driven)
    violations: List[Violation] = []

    if driven >= max_driving_seconds:
        overage = driven - max_driving_seconds
        severity = _severity_for_limit_overage(overage)
        violations.append(
            Violation(
                violation_type=ViolationType.DRIVING_LIMIT,
                severity=severity,
                rule_ref="§ 395.3(a)(3)(i)",
                description=(
                    f"Driver has used {driven / 3600:.2f}h of driving "
                    f"(limit: {limit_h:.0f}h). Immediate rest required."
                ),
                detected_at=now,
                overage_seconds=overage,
            )
        )
    elif remaining <= WARNING_THRESHOLD_SECONDS:
        violations.append(
            Violation(
                violation_type=ViolationType.DRIVING_LIMIT,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.3(a)(3)(i)",
                description=(
                    f"Driver has {remaining / 60:.0f} min of driving remaining "
                    f"({limit_h:.0f}h limit)."
                ),
                detected_at=now,
            )
        )

    return remaining, violations


# ── 2. 14-Hour Duty Window ────────────────────────────────────────────────

def check_duty_window(
    state: StateMachineResult,
    now: datetime,
    *,
    max_duty_window_seconds: float = MAX_DUTY_WINDOW_SECONDS,
) -> tuple[float, List[Violation]]:
    """§ 395.3(a)(2) — Duty window from first on-duty moment (default 14h).

    ``max_duty_window_seconds`` may be raised for adverse (§ 395.1(b)) or
    § 395.1(o) 16-hour short-haul exception.

    VIOLATION/CRITICAL only when elapsed ≥ limit **and** the driver is currently Driving.
    WARNING when approaching the limit while still driving.

    Returns:
        (duty_window_remaining_seconds, violations)
    """
    elapsed = state.duty_window_elapsed_seconds
    limit_h = max_duty_window_seconds / 3600.0
    remaining = max(0.0, max_duty_window_seconds - elapsed)
    violations: List[Violation] = []
    driving = state.is_currently_driving

    if elapsed >= max_duty_window_seconds and driving:
        overage = elapsed - max_duty_window_seconds
        severity = _severity_for_limit_overage(overage)
        violations.append(
            Violation(
                violation_type=ViolationType.DUTY_WINDOW,
                severity=severity,
                rule_ref="§ 395.3(a)(2)",
                description=(
                    f"Driver has exceeded the {limit_h:.0f}-hour duty window "
                    f"by {overage / 3600:.2f}h."
                ),
                detected_at=now,
                overage_seconds=overage,
            )
        )
    elif remaining <= WARNING_THRESHOLD_SECONDS and driving:
        violations.append(
            Violation(
                violation_type=ViolationType.DUTY_WINDOW,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.3(a)(2)",
                description=(
                    f"Only {remaining / 60:.0f} min remaining in {limit_h:.0f}h duty window."
                ),
                detected_at=now,
            )
        )

    return remaining, violations


# ── 3. 30-Minute Rest Break ───────────────────────────────────────────────

def check_rest_break(
    state: StateMachineResult,
    now: datetime,
) -> tuple[bool, List[Violation]]:
    """§ 395.3(a)(3)(ii) — 30-min break required after 8 cumulative driving hours.

    VIOLATION only when the accumulator ≥ 8h **and** the driver is currently Driving.
    Break resets on any consecutive non-driving ≥ 30 minutes (including ON_DUTY).
    Missed break stays VIOLATION (SERIOUS); no CRITICAL promotion on break overage.

    Returns:
        (break_required: bool, violations)
    """
    driving_since_break = state.driving_since_break_seconds
    break_required = driving_since_break >= MAX_DRIVING_BEFORE_BREAK_SECONDS
    violations: List[Violation] = []
    driving = state.is_currently_driving

    if break_required and driving:
        violations.append(
            Violation(
                violation_type=ViolationType.REST_BREAK,
                severity=ViolationSeverity.VIOLATION,
                rule_ref="§ 395.3(a)(3)(ii)",
                description=(
                    f"Driver has driven {driving_since_break / 3600:.2f}h without "
                    f"a 30-minute break. Break required immediately."
                ),
                detected_at=now,
                overage_seconds=driving_since_break - MAX_DRIVING_BEFORE_BREAK_SECONDS,
            )
        )
    elif (
        driving
        and driving_since_break
        >= (MAX_DRIVING_BEFORE_BREAK_SECONDS - WARNING_THRESHOLD_SECONDS)
    ):
        violations.append(
            Violation(
                violation_type=ViolationType.REST_BREAK,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.3(a)(3)(ii)",
                description=(
                    f"Driver approaching 8-hour driving threshold without break. "
                    f"{(MAX_DRIVING_BEFORE_BREAK_SECONDS - driving_since_break) / 60:.0f} min remaining."
                ),
                detected_at=now,
            )
        )

    return break_required, violations


# ── 4. 60/70-Hour Weekly Cycle ────────────────────────────────────────────

def check_weekly_cycle(
    weekly_duty_seconds: float,
    now: datetime,
) -> tuple[float, float, List[Violation]]:
    """§ 395.3(b) — 60/70-hour rolling weekly cycle.

    Uses settings.WEEKLY_CYCLE_LIMIT_HOURS (default 70h for 8-day cycle).
    WARNING when used >90% of the cycle limit (remaining ≤10%).
    Cycle exceeded stays VIOLATION (SERIOUS).

    Returns:
        (hours_used, hours_remaining, violations)
    """
    limit_seconds = settings.WEEKLY_CYCLE_LIMIT_HOURS * 3600.0
    hours_used = weekly_duty_seconds / 3600.0
    hours_remaining = max(0.0, (limit_seconds - weekly_duty_seconds) / 3600.0)
    violations: List[Violation] = []
    # used ≥ 90% ⇔ remaining ≤ 10% (compare on used side to avoid 1.0-0.9 float drift)
    warn_used_seconds = limit_seconds * WEEKLY_WARNING_USED_FRACTION

    if weekly_duty_seconds >= limit_seconds:
        violations.append(
            Violation(
                violation_type=ViolationType.WEEKLY_CYCLE,
                severity=ViolationSeverity.VIOLATION,
                rule_ref="§ 395.3(b)",
                description=(
                    f"Driver has used {hours_used:.1f}h of duty time "
                    f"(limit: {settings.WEEKLY_CYCLE_LIMIT_HOURS:.0f}h). "
                    f"34-hour restart required."
                ),
                detected_at=now,
                overage_seconds=weekly_duty_seconds - limit_seconds,
            )
        )
    elif weekly_duty_seconds >= warn_used_seconds:
        violations.append(
            Violation(
                violation_type=ViolationType.WEEKLY_CYCLE,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.3(b)",
                description=(
                    f"Only {hours_remaining:.1f}h of weekly duty time remaining "
                    f"(limit: {settings.WEEKLY_CYCLE_LIMIT_HOURS:.0f}h; "
                    f"{hours_used / settings.WEEKLY_CYCLE_LIMIT_HOURS * 100:.0f}% used)."
                ),
                detected_at=now,
            )
        )

    return hours_used, hours_remaining, violations


# ── 5. 34-Hour Restart ────────────────────────────────────────────────────

def check_restart(
    state: StateMachineResult,
    now: datetime,
) -> List[Violation]:
    """§ 395.3(c) — 34-hour consecutive off-duty restart (cycle reset only).

    As of ``fmcsa-us-property@2.5.0`` the obsolete two 1–5 AM home-terminal
    periods gate is not enforced, so this calculator never emits findings.
    ``RESTART_INVALID`` remains in ``ViolationType`` for historical audit rows.
    """
    _ = (state, now)
    return []
