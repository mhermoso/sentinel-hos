"""Versioned Rule Pack — the compliance evaluation orchestrator.

Binds all 5 calculators to a specific SemVer release (ADR-004).
Accepts a DriverTimeline, runs the state machine, applies all rules,
and returns a ComplianceResult in < 20 ms for typical driver histories.

Usage:
    pack = RulePack(version="fmcsa-us-property@1.2.0")
    result = pack.evaluate(timeline, inputs_hash="sha256...")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from app.domains.engine.calculators import (
    check_driving_limit,
    check_duty_window,
    check_rest_break,
    check_restart,
    check_weekly_cycle,
)
from app.domains.engine.schemas import (
    ComplianceResult,
    DriverTimeline,
    Violation,
)
from app.domains.engine.replay import truncate_timeline_to
from app.domains.engine.state_machine import run_state_machine

logger = logging.getLogger("dcw.engine.rule_pack")


class RulePack:
    """Versioned rule set for 49 CFR Part 395 U.S. property-carrying rules.

    Attributes:
        version: SemVer-style identifier bound to each audit record.
    """

    def __init__(self, version: str = "fmcsa-us-property@1.2.0") -> None:
        self.version = version

    def evaluate(
        self,
        timeline: DriverTimeline,
        inputs_hash: str,
        weekly_duty_seconds: float = 0.0,
        as_of: Optional[datetime] = None,
    ) -> ComplianceResult:
        """Run all 5 HOS rule calculators against the driver timeline.

        Args:
            timeline: Ordered sequence of HOS events for the driver.
            inputs_hash: SHA-256 digest of the canonical inputs (ADR-003).
            weekly_duty_seconds: Pre-computed rolling weekly duty seconds
                from the last 7 or 8 days (passed in from repository layer).
            as_of: Point-in-time for replay evaluation.  When set, the
                timeline is truncated and a synthetic close event is added.

        Returns:
            ComplianceResult with remaining times and any violations.
        """
        now = as_of if as_of is not None else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

        eval_timeline = truncate_timeline_to(timeline, now) if as_of is not None else timeline

        logger.debug(
            "Evaluating rule pack %s for driver %s (%d events, as_of=%s)",
            self.version,
            timeline.driver_id,
            len(eval_timeline.events),
            now.isoformat(),
        )

        # ── Run state machine ─────────────────────────────────────────
        state = run_state_machine(eval_timeline)

        # ── Apply each rule calculator ────────────────────────────────
        all_violations: List[Violation] = []

        # 1. 11-hour driving limit
        driving_remaining, drive_violations = check_driving_limit(state, now)
        all_violations.extend(drive_violations)

        # 2. 14-hour duty window
        duty_remaining, duty_violations = check_duty_window(state, now)
        all_violations.extend(duty_violations)

        # 3. 30-minute rest break
        break_required, break_violations = check_rest_break(state, now)
        all_violations.extend(break_violations)

        # 4. 60/70-hour weekly cycle
        hours_used, hours_remaining, weekly_violations = check_weekly_cycle(
            weekly_duty_seconds, now
        )
        all_violations.extend(weekly_violations)

        # 5. 34-hour restart
        restart_violations = check_restart(state, now)
        all_violations.extend(restart_violations)

        result = ComplianceResult(
            driver_id=timeline.driver_id,
            tenant_id=timeline.tenant_id,
            evaluated_at=now,
            rule_pack_version=self.version,
            inputs_hash=inputs_hash,
            driving_remaining_seconds=driving_remaining,
            duty_window_remaining_seconds=duty_remaining,
            break_required=break_required,
            weekly_hours_used=hours_used,
            weekly_hours_remaining=hours_remaining,
            violations=all_violations,
        )

        logger.info(
            "Compliance result driver=%s compliant=%s violations=%d",
            timeline.driver_id,
            result.is_compliant,
            len(all_violations),
        )
        return result
