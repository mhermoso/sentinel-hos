"""Versioned Rule Pack — thin router over ruleset pack modules (ADR-004).

Selects Ruleset A/B/C/D from the driver profile (PDF §2), then dispatches
to the corresponding pack module (A/B federal; C/D Texas).

Usage:
    pack = RulePack(version="fmcsa-us-property@2.5.0")
    result = pack.evaluate(timeline, inputs_hash="sha256...")
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Sequence

from app.domains.engine.packs.router import evaluate_with_router
from app.domains.engine.schemas import (
    ComplianceResult,
    DayAnnotations,
    DriverProfile,
    DriverTimeline,
    GpsFix,
)

logger = logging.getLogger("dcw.engine.rule_pack")


class RulePack:
    """Versioned evaluation entry point binding audit records to a SemVer pack."""

    def __init__(self, version: str = "fmcsa-us-property@2.5.0") -> None:
        self.version = version

    def evaluate(
        self,
        timeline: DriverTimeline,
        inputs_hash: str,
        weekly_duty_seconds: float = 0.0,
        as_of: Optional[datetime] = None,
        profile: Optional[DriverProfile] = None,
        exemption_ok: Optional[bool] = None,
        gps_fixes: Optional[Sequence[GpsFix]] = None,
        short_haul_failure_days_30: int = 0,
        day_annotations: Optional[DayAnnotations] = None,
        adverse_driving: Optional[bool] = None,
        sixteen_hour_exception: Optional[bool] = None,
    ) -> ComplianceResult:
        """Route to the selected ruleset pack and return a ComplianceResult.

        Args:
            timeline: Ordered sequence of HOS events for the driver.
            inputs_hash: SHA-256 digest of the canonical inputs (ADR-003).
            weekly_duty_seconds: Pre-computed rolling weekly duty seconds
                from the last 7 or 8 days (passed in from repository layer).
            as_of: Point-in-time for replay evaluation.  When set, the
                timeline is truncated and a synthetic close event is added.
            profile: Per-driver ruleset config; defaults to interstate 70/8.
            exemption_ok: Optional force-fallback to base regime when False.
            gps_fixes: Mapped GPS points for short-haul / YM heuristics.
            short_haul_failure_days_30: Effective 8-in-30 failure-day count.
            day_annotations: Per-day exception flags and form & manner evidence.
            adverse_driving: Convenience override for § 395.1(b) day flag.
            sixteen_hour_exception: Convenience override for § 395.1(o) request.

        Returns:
            ComplianceResult with remaining times and any violations.
        """
        logger.debug(
            "RulePack %s evaluate driver=%s profile=%s exemption_ok=%s gps=%d",
            self.version,
            timeline.driver_id,
            None if profile is None else profile.operating_authority.value,
            exemption_ok,
            0 if gps_fixes is None else len(gps_fixes),
        )
        return evaluate_with_router(
            timeline,
            inputs_hash,
            version=self.version,
            weekly_duty_seconds=weekly_duty_seconds,
            as_of=as_of,
            profile=profile,
            exemption_ok=exemption_ok,
            gps_fixes=gps_fixes,
            short_haul_failure_days_30=short_haul_failure_days_30,
            day_annotations=day_annotations,
            adverse_driving=adverse_driving,
            sixteen_hour_exception=sixteen_hour_exception,
        )
