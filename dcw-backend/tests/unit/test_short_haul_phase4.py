"""Phase 4: Federal 150 air-mile short-haul (Ruleset B)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domains.engine.geo import (
    SHORT_HAUL_RADIUS_AIR_MILES,
    haversine_air_miles,
)
from app.domains.engine.packs.fmcsa_us_short_haul import PACK_VERSION
from app.domains.engine.replay import truncate_timeline_to
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import (
    DriverProfile,
    DriverTimeline,
    GpsFix,
    RulesetId,
    RulesetStatus,
    ViolationSeverity,
    ViolationType,
    WorkReportingLocation,
    default_driver_profile,
)
from app.domains.engine.short_haul import (
    ExemptionFailReason,
    assess_short_haul_exemption,
    eld_8_in_30_findings,
)
from app.domains.engine.state_machine import run_state_machine
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = UTC

# Dallas-ish depot
WRL = WorkReportingLocation(latitude=32.7767, longitude=-96.7970)


def _ts(hours: float) -> datetime:
    return datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _evt(status: CanonicalDutyStatus, hours: float) -> DriverTimeline.HOSEvent:
    return DriverTimeline.HOSEvent(status=status.value, timestamp=_ts(hours))


def _timeline(*events: DriverTimeline.HOSEvent) -> DriverTimeline:
    return DriverTimeline(driver_id="d1", tenant_id="t1", events=list(events))


def _profile(**overrides: object) -> DriverProfile:
    base = default_driver_profile(driver_id="d1", tenant_id="t1")
    data = base.model_dump()
    data.update(
        {
            "short_haul_eligible": True,
            "work_reporting_location": WRL,
            "cdl_required": True,
        }
    )
    data.update(overrides)
    return DriverProfile.model_validate(data)


def _fix(hours: float, lat: float, lon: float) -> GpsFix:
    return GpsFix(latitude=lat, longitude=lon, timestamp=_ts(hours))


def _near_depot(hours: float, dlat: float = 0.0, dlon: float = 0.0) -> GpsFix:
    return _fix(hours, WRL.latitude + dlat, WRL.longitude + dlon)


# ~1° latitude ≈ 60 nmi; 3° ≈ 180 nmi > 150
def _beyond_150(hours: float) -> GpsFix:
    return _fix(hours, WRL.latitude + 3.0, WRL.longitude)


def _state(timeline: DriverTimeline, as_of: datetime):
    return run_state_machine(truncate_timeline_to(timeline, as_of))


# ── Geo ───────────────────────────────────────────────────────────────────


def test_haversine_air_miles_known_distance() -> None:
    # Same point
    assert haversine_air_miles(32.0, -96.0, 32.0, -96.0) == pytest.approx(0.0)
    # ~1° latitude separation ≈ 60 air-miles
    dist = haversine_air_miles(32.0, -96.0, 33.0, -96.0)
    assert dist == pytest.approx(60.0, abs=1.0)
    assert dist < SHORT_HAUL_RADIUS_AIR_MILES


# ── Exemption assessment ──────────────────────────────────────────────────


def test_missing_wrl_fails_closed() -> None:
    profile = _profile(work_reporting_location=None)
    events = [_evt(CanonicalDutyStatus.OFF_DUTY, 0), _evt(CanonicalDutyStatus.DRIVING, 10)]
    as_of = _ts(12)
    result = assess_short_haul_exemption(
        profile=profile,
        state=_state(_timeline(*events), as_of),
        gps_fixes=[_near_depot(10.5)],
        as_of=as_of,
    )
    assert result.ok is False
    assert result.reason == ExemptionFailReason.MISSING_WORK_REPORTING_LOCATION


def test_missing_gps_fails_closed() -> None:
    profile = _profile()
    events = [_evt(CanonicalDutyStatus.OFF_DUTY, 0), _evt(CanonicalDutyStatus.DRIVING, 10)]
    as_of = _ts(12)
    result = assess_short_haul_exemption(
        profile=profile,
        state=_state(_timeline(*events), as_of),
        gps_fixes=[],
        as_of=as_of,
    )
    assert result.ok is False
    assert result.reason == ExemptionFailReason.MISSING_GPS_BREADCRUMBS


def test_beyond_150_air_miles_fails() -> None:
    profile = _profile()
    events = [_evt(CanonicalDutyStatus.OFF_DUTY, 0), _evt(CanonicalDutyStatus.DRIVING, 10)]
    as_of = _ts(12)
    result = assess_short_haul_exemption(
        profile=profile,
        state=_state(_timeline(*events), as_of),
        gps_fixes=[_near_depot(10.5), _beyond_150(11.0)],
        as_of=as_of,
    )
    assert result.ok is False
    assert result.reason == ExemptionFailReason.BEYOND_150_AIR_MILES


def test_cdl_release_window_14h() -> None:
    profile = _profile(cdl_required=True)
    events = [_evt(CanonicalDutyStatus.OFF_DUTY, 0), _evt(CanonicalDutyStatus.DRIVING, 10)]
    as_of = _ts(24.5)  # 14.5h after duty start at _ts(10)
    result = assess_short_haul_exemption(
        profile=profile,
        state=_state(_timeline(*events), as_of),
        gps_fixes=[_near_depot(10.5), _near_depot(20.0)],
        as_of=as_of,
    )
    assert result.ok is False
    assert result.reason == ExemptionFailReason.RELEASE_WINDOW_EXCEEDED


def test_non_cdl_allows_16h_release_window() -> None:
    profile = _profile(cdl_required=False)
    driving = _timeline(
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
    )
    fixes = [_near_depot(10.5), _near_depot(20.0), _near_depot(25.0)]
    # 15h into tour, still driving — OK for non-CDL (16h limit)
    mid_as_of = _ts(25)
    mid = assess_short_haul_exemption(
        profile=profile,
        state=_state(driving, mid_as_of),
        gps_fixes=fixes,
        as_of=mid_as_of,
    )
    assert mid.ok is True

    # Released at 15h after duty start, returned near depot
    released_tl = _timeline(
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
        _evt(CanonicalDutyStatus.OFF_DUTY, 25),
    )
    released_as_of = _ts(25.5)
    released = assess_short_haul_exemption(
        profile=profile,
        state=_state(released_tl, released_as_of),
        gps_fixes=fixes,
        as_of=released_as_of,
    )
    assert released.ok is True


def test_did_not_return_fails() -> None:
    profile = _profile(cdl_required=True)
    tl = _timeline(
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
        _evt(CanonicalDutyStatus.OFF_DUTY, 20),
    )
    as_of = _ts(20.5)
    # In-radius (~120 nmi) but not back at the depot at release
    far_but_under_150 = _fix(19.5, WRL.latitude + 2.0, WRL.longitude)
    result = assess_short_haul_exemption(
        profile=profile,
        state=_state(tl, as_of),
        gps_fixes=[_near_depot(10.5), far_but_under_150],
        as_of=as_of,
    )
    assert result.ok is False
    assert result.reason == ExemptionFailReason.DID_NOT_RETURN


def test_cdl_14h_would_fail_where_non_cdl_passes() -> None:
    events = [_evt(CanonicalDutyStatus.OFF_DUTY, 0), _evt(CanonicalDutyStatus.DRIVING, 10)]
    as_of = _ts(25)  # 15h after duty start
    state = _state(_timeline(*events), as_of)
    fixes = [_near_depot(10.5), _near_depot(24.0)]
    cdl = assess_short_haul_exemption(
        profile=_profile(cdl_required=True),
        state=state,
        gps_fixes=fixes,
        as_of=as_of,
    )
    non_cdl = assess_short_haul_exemption(
        profile=_profile(cdl_required=False),
        state=state,
        gps_fixes=fixes,
        as_of=as_of,
    )
    assert cdl.ok is False
    assert non_cdl.ok is True


# ── Pack evaluation ───────────────────────────────────────────────────────


def test_short_haul_holds_suppresses_break() -> None:
    """8h+ driving without break: A would violate; B suppresses while exempt."""
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
    ]
    profile = _profile()
    fixes = [_near_depot(h) for h in (10.5, 12.0, 14.0, 16.0, 18.0)]
    # 8.5h driving at as_of=_ts(18.5)
    result = RulePack().evaluate(
        _timeline(*events),
        inputs_hash="h",
        as_of=_ts(18.5),
        profile=profile,
        gps_fixes=fixes,
    )
    assert result.selected_ruleset == RulesetId.B
    assert result.ruleset_status == RulesetStatus.IMPLEMENTED
    assert result.ruleset_pack_id == "fmcsa_us_short_haul"
    assert result.rule_pack_version == PACK_VERSION
    assert result.break_required is False
    assert not any(v.violation_type == ViolationType.REST_BREAK for v in result.violations)
    assert not any(v.violation_type == ViolationType.RULESET_UNSUPPORTED for v in result.violations)
    # 11h − 8.5h = 2.5h remaining
    assert result.driving_remaining_seconds == pytest.approx(2.5 * 3600.0)


def test_exemption_lost_falls_back_to_a_with_rods() -> None:
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
    ]
    result = RulePack().evaluate(
        _timeline(*events),
        inputs_hash="h",
        as_of=_ts(18.5),
        profile=_profile(),
        gps_fixes=[_near_depot(10.5), _beyond_150(12.0)],
        short_haul_failure_days_30=1,
    )
    types = {v.violation_type for v in result.violations}
    assert ViolationType.EXEMPTION_LOST in types
    assert ViolationType.RODS_REQUIRED in types
    assert result.selected_ruleset == RulesetId.B
    assert result.ruleset_status == RulesetStatus.IMPLEMENTED
    # Fallback A enforces break after 8h driving
    assert result.break_required is True
    assert any(v.violation_type == ViolationType.REST_BREAK for v in result.violations)


def test_eld_8_in_30_severity_tiers() -> None:
    now = _ts(12)
    assert eld_8_in_30_findings(4, now=now) == []
    warn = eld_8_in_30_findings(5, now=now)
    assert warn[0].severity == ViolationSeverity.WARNING
    urgent = eld_8_in_30_findings(7, now=now)
    assert urgent[0].severity == ViolationSeverity.VIOLATION
    viol = eld_8_in_30_findings(9, now=now)
    assert viol[0].severity == ViolationSeverity.CRITICAL
    assert viol[0].violation_type == ViolationType.ELD_REQUIRED_8_IN_30


def test_pack_emits_eld_required_at_5_failures() -> None:
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 10),
    ]
    result = RulePack().evaluate(
        _timeline(*events),
        inputs_hash="h",
        as_of=_ts(12),
        profile=_profile(),
        gps_fixes=[_near_depot(10.5), _near_depot(11.5)],
        short_haul_failure_days_30=5,
    )
    eld = [v for v in result.violations if v.violation_type == ViolationType.ELD_REQUIRED_8_IN_30]
    assert len(eld) == 1
    assert eld[0].severity == ViolationSeverity.WARNING


def test_router_selects_implemented_b() -> None:
    profile = _profile()
    result = RulePack().evaluate(
        _timeline(_evt(CanonicalDutyStatus.DRIVING, 0)),
        inputs_hash="h",
        as_of=_ts(1),
        profile=profile,
        gps_fixes=[_near_depot(0.5)],
    )
    assert result.selected_ruleset == RulesetId.B
    assert result.ruleset_status == RulesetStatus.IMPLEMENTED
    assert result.rule_pack_version == "fmcsa-us-short-haul@1.0.0"
