"""Daily ruleset selection (PDF §2) and pack dispatch."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Mapping, Optional, Sequence

from app.domains.engine.packs.base import RulePackModule
from app.domains.engine.packs.fmcsa_us_property import fmcsa_us_property_pack
from app.domains.engine.packs.fmcsa_us_short_haul import fmcsa_us_short_haul_pack
from app.domains.engine.packs.tx_intrastate import tx_intrastate_pack
from app.domains.engine.packs.tx_short_haul import tx_short_haul_pack
from app.domains.engine.schemas import (
    ComplianceResult,
    DayAnnotations,
    DriverProfile,
    DriverTimeline,
    GpsFix,
    OperatingAuthority,
    RulesetId,
    default_driver_profile,
)

logger = logging.getLogger("dcw.engine.packs.router")

PACK_BY_RULESET: Mapping[RulesetId, RulePackModule] = {
    RulesetId.A: fmcsa_us_property_pack,
    RulesetId.B: fmcsa_us_short_haul_pack,
    RulesetId.C: tx_intrastate_pack,
    RulesetId.D: tx_short_haul_pack,
}


def select_ruleset(
    profile: DriverProfile,
    exemption_ok: Optional[bool] = None,
) -> RulesetId:
    """Select Ruleset A/B/C/D from driver profile (PDF §2 daily decision).

    Base regime: INTERSTATE → A, TX_INTRASTATE → C.
    When ``short_haul_eligible`` and exemption is not explicitly failed,
    select B (federal short-haul) or D (TX short-haul).

    ``exemption_ok`` may force base-regime selection when explicitly ``False``
    (e.g. external pre-check). ``None``/``True`` keep the short-haul path;
    Ruleset B evaluates day conditions and may fall back to A clocks internally.
    """
    if profile.operating_authority == OperatingAuthority.TX_INTRASTATE:
        base = RulesetId.C
    else:
        base = RulesetId.A

    if not profile.short_haul_eligible:
        return base

    if exemption_ok is False:
        return base

    return RulesetId.B if base == RulesetId.A else RulesetId.D


def evaluate_with_router(
    timeline: DriverTimeline,
    inputs_hash: str,
    *,
    version: str,
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
    """Resolve profile → ruleset → pack module and evaluate."""
    resolved = profile or default_driver_profile(
        driver_id=timeline.driver_id,
        tenant_id=timeline.tenant_id,
    )
    ruleset = select_ruleset(resolved, exemption_ok=exemption_ok)
    pack = PACK_BY_RULESET[ruleset]

    logger.debug(
        "Ruleset router driver=%s authority=%s short_haul=%s → %s (%s implemented=%s)",
        timeline.driver_id,
        resolved.operating_authority.value,
        resolved.short_haul_eligible,
        ruleset.value,
        pack.pack_id,
        pack.implemented,
    )

    return pack.evaluate(
        timeline,
        inputs_hash,
        version=version,
        weekly_duty_seconds=weekly_duty_seconds,
        as_of=as_of,
        profile=resolved,
        gps_fixes=gps_fixes,
        short_haul_failure_days_30=short_haul_failure_days_30,
        day_annotations=day_annotations,
        adverse_driving=adverse_driving,
        sixteen_hour_exception=sixteen_hour_exception,
    )
