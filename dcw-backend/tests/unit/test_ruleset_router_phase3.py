"""Phase 3: driver profile defaults, ruleset selection, and stub packs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.domains.engine.packs.router import select_ruleset
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import (
    DriverProfile,
    DriverTimeline,
    HosCycle,
    OperatingAuthority,
    RulesetId,
    RulesetStatus,
    ViolationType,
    default_driver_profile,
)
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = timezone.utc


def _ts(hours: float) -> datetime:
    return datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _evt(status: CanonicalDutyStatus, hours: float) -> DriverTimeline.HOSEvent:
    return DriverTimeline.HOSEvent(status=status.value, timestamp=_ts(hours))


def _timeline(*events: DriverTimeline.HOSEvent) -> DriverTimeline:
    return DriverTimeline(driver_id="d1", tenant_id="t1", events=list(events))


def _profile(**overrides: object) -> DriverProfile:
    base = default_driver_profile(driver_id="d1", tenant_id="t1")
    data = base.model_dump()
    data.update(overrides)
    return DriverProfile.model_validate(data)


# ── Defaults → Ruleset A ──────────────────────────────────────────────────


def test_default_profile_selects_ruleset_a() -> None:
    profile = default_driver_profile(driver_id="d1", tenant_id="t1")
    assert profile.operating_authority == OperatingAuthority.INTERSTATE
    assert profile.short_haul_eligible is False
    assert profile.cdl_required is True
    assert profile.cycle == HosCycle.CYCLE_70_8
    assert profile.home_terminal_timezone == settings.DEFAULT_HOME_TERMINAL_TIMEZONE
    assert select_ruleset(profile) == RulesetId.A


def test_default_profile_evaluates_ruleset_a_fully() -> None:
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
    ]
    pack = RulePack()
    result = pack.evaluate(
        _timeline(*events),
        inputs_hash="h",
        as_of=_ts(12),
    )
    assert result.selected_ruleset == RulesetId.A
    assert result.ruleset_status == RulesetStatus.IMPLEMENTED
    assert result.ruleset_pack_id == "fmcsa_us_property"
    assert result.driving_remaining_seconds == pytest.approx(9 * 3600.0)
    assert not any(
        v.violation_type == ViolationType.RULESET_UNSUPPORTED for v in result.violations
    )


# ── TX / short-haul pack selection ────────────────────────────────────────


def test_tx_intrastate_selects_c_and_is_implemented() -> None:
    profile = _profile(operating_authority=OperatingAuthority.TX_INTRASTATE)
    assert select_ruleset(profile) == RulesetId.C

    pack = RulePack()
    result = pack.evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.DRIVING, 8),
        ),
        inputs_hash="h",
        as_of=_ts(10),
        profile=profile,
    )
    assert result.selected_ruleset == RulesetId.C
    assert result.ruleset_status == RulesetStatus.IMPLEMENTED
    assert result.ruleset_pack_id == "tx_intrastate"
    assert result.rule_pack_version == "tx-intrastate@1.0.0"
    assert result.driving_remaining_seconds == pytest.approx(10 * 3600.0)
    assert not any(
        v.violation_type == ViolationType.RULESET_UNSUPPORTED for v in result.violations
    )


def test_interstate_short_haul_selects_b_implemented() -> None:
    profile = _profile(
        short_haul_eligible=True,
        work_reporting_location={"latitude": 32.7767, "longitude": -96.7970},
    )
    assert select_ruleset(profile) == RulesetId.B

    from app.domains.engine.schemas import GpsFix

    result = RulePack().evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.DRIVING, 10),
        ),
        inputs_hash="h",
        as_of=_ts(12),
        profile=profile,
        gps_fixes=[
            GpsFix(latitude=32.7767, longitude=-96.7970, timestamp=_ts(10.5)),
            GpsFix(latitude=32.78, longitude=-96.80, timestamp=_ts(11.5)),
        ],
    )
    assert result.selected_ruleset == RulesetId.B
    assert result.ruleset_status == RulesetStatus.IMPLEMENTED
    assert result.ruleset_pack_id == "fmcsa_us_short_haul"
    assert result.rule_pack_version == "fmcsa-us-short-haul@1.0.0"
    assert not any(
        v.violation_type == ViolationType.RULESET_UNSUPPORTED for v in result.violations
    )


def test_tx_short_haul_selects_d_implemented() -> None:
    profile = _profile(
        operating_authority=OperatingAuthority.TX_INTRASTATE,
        short_haul_eligible=True,
    )
    assert select_ruleset(profile) == RulesetId.D
    result = RulePack().evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.ON_DUTY, 8),
        ),
        inputs_hash="h",
        as_of=_ts(9),
        profile=profile,
    )
    assert result.selected_ruleset == RulesetId.D
    assert result.ruleset_status == RulesetStatus.IMPLEMENTED
    assert result.ruleset_pack_id == "tx_short_haul"
    assert result.rule_pack_version == "tx-short-haul@1.0.0"


def test_exemption_failed_falls_back_to_base_regime() -> None:
    interstate = _profile(short_haul_eligible=True)
    assert select_ruleset(interstate, exemption_ok=False) == RulesetId.A

    tx = _profile(
        operating_authority=OperatingAuthority.TX_INTRASTATE,
        short_haul_eligible=True,
    )
    assert select_ruleset(tx, exemption_ok=False) == RulesetId.C


# ── Cycle enum parsing ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("60_7", HosCycle.CYCLE_60_7),
        ("70_8", HosCycle.CYCLE_70_8),
        ("TX_70_7", HosCycle.CYCLE_TX_70_7),
        ("60/7", HosCycle.CYCLE_60_7),
        ("70/8", HosCycle.CYCLE_70_8),
        (HosCycle.CYCLE_70_8, HosCycle.CYCLE_70_8),
    ],
)
def test_hos_cycle_parse(raw: str | HosCycle, expected: HosCycle) -> None:
    assert HosCycle.parse(raw) == expected


def test_driver_profile_accepts_cycle_aliases() -> None:
    profile = DriverProfile(
        driver_id="d1",
        tenant_id="t1",
        cycle="60/7",
        home_terminal_timezone="America/Chicago",
    )
    assert profile.cycle == HosCycle.CYCLE_60_7


def test_rule_pack_default_version_is_2_3_0() -> None:
    assert RulePack().version == "fmcsa-us-property@2.5.0"
