"""Texas intrastate HOS calculators (37 TAC §4.12 / PDF §5.2).

Clocks:
  1. 12-hour driving after 8h off — OOS-risk → CRITICAL on any overage
  2. 15-hour accumulated ON+D(+YM) — OOS-risk → CRITICAL on any overage
  3. 70h / 7-day cycle only (no 60/7)
  4. No 30-minute break rule

Severity (PDF §8.3): Texas 12/15 overages are CRITICAL (8h roadside OOS).
ADVISORY (WARNING) within 60 min while still driving.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from app.domains.engine.calculators import (
    WARNING_THRESHOLD_SECONDS,
    WEEKLY_WARNING_USED_FRACTION,
)
from app.domains.engine.schemas import (
    Violation,
    ViolationSeverity,
    ViolationType,
)
from app.domains.engine.tx_state_machine import (
    TX_MAX_DRIVING_SECONDS,
    TX_MAX_DUTY_SECONDS,
    TxStateMachineResult,
)

logger = logging.getLogger("dcw.engine.tx_calculators")

TX_WEEKLY_LIMIT_HOURS: float = 70.0
TX_WEEKLY_LIMIT_SECONDS: float = TX_WEEKLY_LIMIT_HOURS * 3600.0
TX_RULE_REF_DRIVING = "37 TAC §4.12 (12h driving)"
TX_RULE_REF_DUTY = "37 TAC §4.12 (15h on-duty)"
TX_RULE_REF_WEEKLY = "37 TAC §4.12 (70h/7d)"


def check_tx_driving_limit(
    state: TxStateMachineResult,
    now: datetime,
) -> tuple[float, List[Violation]]:
    """12h driving after 8 consecutive hours off. Overage → CRITICAL (OOS)."""
    driven = (
        state.current_shift.cumulative_driving_seconds if state.current_shift else 0.0
    )
    remaining = max(0.0, TX_MAX_DRIVING_SECONDS - driven)
    violations: List[Violation] = []

    if driven >= TX_MAX_DRIVING_SECONDS:
        overage = driven - TX_MAX_DRIVING_SECONDS
        violations.append(
            Violation(
                violation_type=ViolationType.TX_DRIVING_LIMIT,
                severity=ViolationSeverity.CRITICAL,
                rule_ref=TX_RULE_REF_DRIVING,
                description=(
                    f"Texas 12h driving limit exceeded by {overage / 3600:.2f}h "
                    f"({driven / 3600:.2f}h driven). OOS-risk: 8 consecutive hours."
                ),
                detected_at=now,
                overage_seconds=overage,
            )
        )
    elif remaining <= WARNING_THRESHOLD_SECONDS and state.is_currently_driving:
        violations.append(
            Violation(
                violation_type=ViolationType.TX_DRIVING_LIMIT,
                severity=ViolationSeverity.WARNING,
                rule_ref=TX_RULE_REF_DRIVING,
                description=(
                    f"Driver has {remaining / 60:.0f} min of Texas driving remaining "
                    f"(12h limit)."
                ),
                detected_at=now,
            )
        )

    return remaining, violations


def check_tx_duty_limit(
    state: TxStateMachineResult,
    now: datetime,
) -> tuple[float, List[Violation]]:
    """15h accumulated ON+D(+YM). Violation only while Driving after 15h. OOS → CRITICAL."""
    accumulated = state.accumulated_duty_seconds
    remaining = max(0.0, TX_MAX_DUTY_SECONDS - accumulated)
    violations: List[Violation] = []
    driving = state.is_currently_driving

    if accumulated >= TX_MAX_DUTY_SECONDS and driving:
        overage = accumulated - TX_MAX_DUTY_SECONDS
        violations.append(
            Violation(
                violation_type=ViolationType.TX_DUTY_LIMIT,
                severity=ViolationSeverity.CRITICAL,
                rule_ref=TX_RULE_REF_DUTY,
                description=(
                    f"Texas 15h accumulated on-duty limit exceeded by "
                    f"{overage / 3600:.2f}h ({accumulated / 3600:.2f}h ON+D). "
                    f"OOS-risk: 8 consecutive hours."
                ),
                detected_at=now,
                overage_seconds=overage,
            )
        )
    elif remaining <= WARNING_THRESHOLD_SECONDS and driving:
        violations.append(
            Violation(
                violation_type=ViolationType.TX_DUTY_LIMIT,
                severity=ViolationSeverity.WARNING,
                rule_ref=TX_RULE_REF_DUTY,
                description=(
                    f"Only {remaining / 60:.0f} min remaining on Texas 15h "
                    f"accumulated on-duty limit."
                ),
                detected_at=now,
            )
        )

    return remaining, violations


def check_tx_weekly_cycle(
    weekly_duty_seconds: float,
    now: datetime,
) -> tuple[float, float, List[Violation]]:
    """70h / 7-day cycle only (no 60/7 option)."""
    hours_used = weekly_duty_seconds / 3600.0
    hours_remaining = max(
        0.0, (TX_WEEKLY_LIMIT_SECONDS - weekly_duty_seconds) / 3600.0
    )
    violations: List[Violation] = []
    warn_used = TX_WEEKLY_LIMIT_SECONDS * WEEKLY_WARNING_USED_FRACTION

    if weekly_duty_seconds >= TX_WEEKLY_LIMIT_SECONDS:
        violations.append(
            Violation(
                violation_type=ViolationType.WEEKLY_CYCLE,
                severity=ViolationSeverity.VIOLATION,
                rule_ref=TX_RULE_REF_WEEKLY,
                description=(
                    f"Driver has used {hours_used:.1f}h of duty time "
                    f"(Texas limit: {TX_WEEKLY_LIMIT_HOURS:.0f}h / 7 days). "
                    f"34-hour restart required."
                ),
                detected_at=now,
                overage_seconds=weekly_duty_seconds - TX_WEEKLY_LIMIT_SECONDS,
            )
        )
    elif weekly_duty_seconds >= warn_used:
        violations.append(
            Violation(
                violation_type=ViolationType.WEEKLY_CYCLE,
                severity=ViolationSeverity.WARNING,
                rule_ref=TX_RULE_REF_WEEKLY,
                description=(
                    f"Only {hours_remaining:.1f}h of Texas weekly duty remaining "
                    f"(limit: {TX_WEEKLY_LIMIT_HOURS:.0f}h / 7 days; "
                    f"{hours_used / TX_WEEKLY_LIMIT_HOURS * 100:.0f}% used)."
                ),
                detected_at=now,
            )
        )

    return hours_used, hours_remaining, violations
