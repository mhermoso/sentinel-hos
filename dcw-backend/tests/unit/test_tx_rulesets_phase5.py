"""Phase 5: Texas Ruleset C clocks and Ruleset D short-haul exemption."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.engine.packs.tx_intrastate import tx_intrastate_pack
from app.domains.engine.packs.tx_short_haul import (
    assess_tx_short_haul_exemption,
    tx_short_haul_pack,
)
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import (
    DriverProfile,
    DriverTimeline,
    GpsFix,
    HosCycle,
    OperatingAuthority,
    RulesetId,
    RulesetStatus,
    ViolationSeverity,
    ViolationType,
    WorkReportingLocation,
    default_driver_profile,
)
from app.domains.engine.short_haul import ExemptionFailReason
from app.domains.engine.tx_calculators import TX_WEEKLY_LIMIT_SECONDS
from app.domains.engine.tx_state_machine import run_tx_state_machine
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = timezone.utc
DALLAS = WorkReportingLocation(latitude=32.7767, longitude=-96.7970)


def _ts(hours: float) -> datetime:
    return datetime(2026, 3, 1, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _evt(status: CanonicalDutyStatus, hours: float) -> DriverTimeline.HOSEvent:
    return DriverTimeline.HOSEvent(status=status.value, timestamp=_ts(hours))


def _timeline(*events: DriverTimeline.HOSEvent) -> DriverTimeline:
    return DriverTimeline(driver_id="d1", tenant_id="t1", events=list(events))


def _tx_profile(**overrides: object) -> DriverProfile:
    base = default_driver_profile(driver_id="d1", tenant_id="t1")
    data = base.model_dump()
    data.update(
        {
            "operating_authority": OperatingAuthority.TX_INTRASTATE,
            "cycle": HosCycle.CYCLE_TX_70_7,
            "work_reporting_location": DALLAS,
        }
    )
    data.update(overrides)
    return DriverProfile.model_validate(data)


# ── Ruleset C clocks ──────────────────────────────────────────────────────


def test_tx_8h_reset_not_10h() -> None:
    """After 8h OFF, driving clocks reset (federal would still need 10h)."""
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 8),
        _evt(CanonicalDutyStatus.OFF_DUTY, 10),  # 2h drive
    ]
    state = run_tx_state_machine(_timeline(*events))
    assert state.current_shift is not None
    assert state.current_shift.cumulative_driving_seconds == pytest.approx(2 * 3600.0)


def test_tx_12h_driving_limit_critical_oos() -> None:
    profile = _tx_profile()
    result = tx_intrastate_pack.evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.DRIVING, 8),
        ),
        inputs_hash="h",
        version="tx-intrastate@1.0.0",
        as_of=_ts(8 + 12 + 0.1),  # 12.1h driving
        profile=profile,
    )
    viols = [v for v in result.violations if v.violation_type == ViolationType.TX_DRIVING_LIMIT]
    assert len(viols) == 1
    assert viols[0].severity == ViolationSeverity.CRITICAL
    assert result.driving_remaining_seconds == 0.0


def test_tx_15h_is_accumulated_not_wall_clock() -> None:
    """OFF mid-day does not consume the 15h accumulator (unlike federal 14h)."""
    # 8h OFF reset, then 10h ON, 3h OFF, 5h ON = 15h accumulated over 18h wall
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.ON_DUTY, 8),
        _evt(CanonicalDutyStatus.OFF_DUTY, 18),  # 10h ON
        _evt(CanonicalDutyStatus.ON_DUTY, 21),  # 3h OFF (not counted)
        _evt(CanonicalDutyStatus.DRIVING, 26),  # +5h ON → 15h accum, now driving
    ]
    state = run_tx_state_machine(_timeline(*events))
    assert state.accumulated_duty_seconds == pytest.approx(15 * 3600.0)
    # Wall clock from first duty is 18h
    assert state.duty_window_elapsed_seconds == pytest.approx(18 * 3600.0)

    result = tx_intrastate_pack.evaluate(
        _timeline(*events),
        inputs_hash="h",
        version="tx-intrastate@1.0.0",
        as_of=_ts(26.25),  # driving after 15h accumulated ON+D
        profile=_tx_profile(),
    )
    duty_viols = [v for v in result.violations if v.violation_type == ViolationType.TX_DUTY_LIMIT]
    assert len(duty_viols) == 1
    assert duty_viols[0].severity == ViolationSeverity.CRITICAL


def test_tx_15h_no_violation_while_not_driving() -> None:
    result = tx_intrastate_pack.evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.ON_DUTY, 8),
        ),
        inputs_hash="h",
        version="tx-intrastate@1.0.0",
        as_of=_ts(8 + 16),  # 16h ON, not driving
        profile=_tx_profile(),
    )
    assert not any(v.violation_type == ViolationType.TX_DUTY_LIMIT for v in result.violations)


def test_tx_no_rest_break_rule() -> None:
    # 9h continuous driving — federal would require break; TX must not.
    result = tx_intrastate_pack.evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.DRIVING, 8),
        ),
        inputs_hash="h",
        version="tx-intrastate@1.0.0",
        as_of=_ts(8 + 9),
        profile=_tx_profile(),
    )
    assert result.break_required is False
    assert not any(v.violation_type == ViolationType.REST_BREAK for v in result.violations)


def test_tx_weekly_70_7_only() -> None:
    result = tx_intrastate_pack.evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.ON_DUTY, 8),
        ),
        inputs_hash="h",
        version="tx-intrastate@1.0.0",
        weekly_duty_seconds=TX_WEEKLY_LIMIT_SECONDS + 60,
        as_of=_ts(9),
        profile=_tx_profile(),
    )
    weekly = [v for v in result.violations if v.violation_type == ViolationType.WEEKLY_CYCLE]
    assert len(weekly) == 1
    assert "70" in weekly[0].description
    assert "7 days" in weekly[0].description


def test_tx_sleeper_split_rematch_4_plus_4() -> None:
    """Two SB periods ≥2h totaling ≥8h rematch clocks from end of first period."""
    events = [
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 8),  # shift start
        _evt(CanonicalDutyStatus.SLEEPER_BERTH, 10),  # 2h drive
        _evt(CanonicalDutyStatus.DRIVING, 14),  # 4h SB
        _evt(CanonicalDutyStatus.SLEEPER_BERTH, 16),  # 2h drive
        _evt(CanonicalDutyStatus.DRIVING, 20),  # 4h SB — pair completes
        _evt(CanonicalDutyStatus.OFF_DUTY, 22),  # 2h drive after rematch
    ]
    state = run_tx_state_machine(_timeline(*events))
    assert state.split_sleeper_active is True
    # Rematch from end of first SB (t=14): driving 14–16 (2h) + 20–22 (2h) = 4h
    assert state.current_shift is not None
    assert state.current_shift.cumulative_driving_seconds == pytest.approx(4 * 3600.0)


def test_tx_pack_via_router() -> None:
    result = RulePack().evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.DRIVING, 8),
        ),
        inputs_hash="h",
        as_of=_ts(10),
        profile=_tx_profile(),
    )
    assert result.selected_ruleset == RulesetId.C
    assert result.ruleset_status == RulesetStatus.IMPLEMENTED
    assert result.ruleset_pack_id == "tx_intrastate"


# ── Ruleset D exemption ───────────────────────────────────────────────────


def test_tx_short_haul_exemption_pass() -> None:
    profile = _tx_profile(short_haul_eligible=True)
    timeline = _timeline(
        _evt(CanonicalDutyStatus.OFF_DUTY, 0),
        _evt(CanonicalDutyStatus.DRIVING, 8),
        _evt(CanonicalDutyStatus.OFF_DUTY, 14),  # 6h tour, released
    )
    # Truncate-style: evaluate at release + a bit into OFF
    as_of = _ts(14.5)
    from app.domains.engine.replay import truncate_timeline_to

    eval_tl = truncate_timeline_to(timeline, as_of)
    fixes = [
        GpsFix(latitude=32.7767, longitude=-96.7970, timestamp=_ts(8.5)),
        GpsFix(latitude=32.78, longitude=-96.80, timestamp=_ts(12)),
        GpsFix(latitude=32.7767, longitude=-96.7970, timestamp=_ts(14)),
    ]
    assessment = assess_tx_short_haul_exemption(
        profile=profile,
        timeline=eval_tl,
        gps_fixes=fixes,
        as_of=as_of,
    )
    assert assessment.ok is True

    result = tx_short_haul_pack.evaluate(
        timeline,
        inputs_hash="h",
        version="tx-short-haul@1.0.0",
        as_of=as_of,
        profile=profile,
        gps_fixes=fixes,
    )
    assert result.selected_ruleset == RulesetId.D
    assert result.ruleset_status == RulesetStatus.IMPLEMENTED
    assert not any(
        v.violation_type in (ViolationType.EXEMPTION_LOST, ViolationType.RODS_REQUIRED)
        for v in result.violations
    )
    # Parallel C clocks: 6h driving → 6h remaining of 12h
    assert result.driving_remaining_seconds == pytest.approx(6 * 3600.0)
    assert result.break_required is False


def test_tx_short_haul_fails_beyond_150_air_miles() -> None:
    profile = _tx_profile(short_haul_eligible=True)
    # ~200+ statute miles north of Dallas
    far = GpsFix(latitude=35.5, longitude=-96.7970, timestamp=_ts(10))
    result = tx_short_haul_pack.evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.DRIVING, 8),
        ),
        inputs_hash="h",
        version="tx-short-haul@1.0.0",
        as_of=_ts(11),
        profile=profile,
        gps_fixes=[
            GpsFix(latitude=32.7767, longitude=-96.7970, timestamp=_ts(8.5)),
            far,
        ],
    )
    types = {v.violation_type for v in result.violations}
    assert ViolationType.EXEMPTION_LOST in types
    assert ViolationType.RODS_REQUIRED in types
    # Still runs C clocks in parallel
    assert result.selected_ruleset == RulesetId.D
    assert result.driving_remaining_seconds > 0


def test_tx_short_haul_fails_12h_release() -> None:
    profile = _tx_profile(short_haul_eligible=True)
    fixes = [
        GpsFix(latitude=32.7767, longitude=-96.7970, timestamp=_ts(8.5)),
        GpsFix(latitude=32.78, longitude=-96.80, timestamp=_ts(14)),
        GpsFix(latitude=32.7767, longitude=-96.7970, timestamp=_ts(20)),
    ]
    result = tx_short_haul_pack.evaluate(
        _timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.DRIVING, 8),
        ),
        inputs_hash="h",
        version="tx-short-haul@1.0.0",
        as_of=_ts(8 + 12.5),  # still on duty past 12h
        profile=profile,
        gps_fixes=fixes,
    )
    lost = [v for v in result.violations if v.violation_type == ViolationType.EXEMPTION_LOST]
    assert len(lost) == 1
    assert "12" in lost[0].description


def test_tx_short_haul_missing_gps_fail_closed() -> None:
    profile = _tx_profile(short_haul_eligible=True)
    assessment = assess_tx_short_haul_exemption(
        profile=profile,
        timeline=_timeline(
            _evt(CanonicalDutyStatus.OFF_DUTY, 0),
            _evt(CanonicalDutyStatus.DRIVING, 8),
            _evt(CanonicalDutyStatus.OFF_DUTY, 10),
        ),
        gps_fixes=[],
        as_of=_ts(10),
    )
    assert assessment.ok is False
    assert assessment.reason == ExemptionFailReason.MISSING_GPS_BREADCRUMBS
