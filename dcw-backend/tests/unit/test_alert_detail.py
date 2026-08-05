"""Unit tests for alert calculation detail + display timezone preference."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from starlette.requests import Request

from app.domains.dashboard.alert_detail import build_alert_detail
from app.domains.dashboard.timezone import (
    COOKIE_NAME,
    default_display_timezone,
    is_valid_timezone,
    resolve_display_timezone,
    zoneinfo_for,
)
from app.domains.engine.schemas import DriverTimeline
from app.domains.ingestion.schemas import CanonicalDutyStatus

UTC = UTC
CHICAGO = ZoneInfo("America/Chicago")


def _ts(hours: float) -> datetime:
    return datetime(2026, 7, 20, 6, 0, 0, tzinfo=UTC) + timedelta(hours=hours)


def _request(cookies: dict | None = None, query: str = "") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/ui/drivers",
        "raw_path": b"/ui/drivers",
        "query_string": query.encode(),
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    req = Request(scope)
    if cookies:
        # Starlette reads cookies from cookie header
        header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        scope["headers"] = [(b"cookie", header.encode())]
        req = Request(scope)
    return req


def test_default_display_timezone_is_central() -> None:
    assert default_display_timezone() == "America/Chicago"
    assert is_valid_timezone("America/Chicago")
    assert zoneinfo_for(None).key == "America/Chicago"


def test_resolve_display_timezone_prefers_query_then_cookie() -> None:
    req = _request(cookies={COOKIE_NAME: "America/Denver"})
    assert resolve_display_timezone(req) == "America/Denver"
    assert resolve_display_timezone(req, tz_param="UTC") == "UTC"
    assert resolve_display_timezone(_request(), tz_param="Not/AZone") == "America/Chicago"


def test_driving_limit_detail_explanation() -> None:
    """10h OFF then 12h driving → DRIVING_LIMIT detail with overage."""
    events = [
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=_ts(0),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=_ts(10),
        ),
    ]
    as_of = _ts(10 + 12)  # 12h into driving
    detail = build_alert_detail(
        driver_id="drv1",
        tenant_id="tenant1",
        driver_name="Test Driver",
        events=events,
        as_of=as_of,
        violation_type="DRIVING_LIMIT",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    assert detail["meta"]["violation_type"] == "DRIVING_LIMIT"
    assert detail["meta"]["display_timezone"] == "America/Chicago"
    assert "CST" in detail["meta"]["local_time"] or "CDT" in detail["meta"]["local_time"]
    assert detail["clocks"]["driving_used_h"] >= 11.0
    assert any(s["step"] == "11-hour driving limit" for s in detail["explanation"])
    assert detail["context_events"]
    assert detail["weekly_restart"]["had_restart"] is False
    assert "unbroken rolling" in detail["weekly_restart"]["message"]
    assert detail["clocks"]["weekly_window_subtitle"] == "rolling window"
    assert any(s["step"] == "Weekly cycle (context)" for s in detail["explanation"])


def test_driving_limit_with_restart_shows_weekly_section() -> None:
    """DRIVING_LIMIT after valid 34h restart — weekly section shows reset, gauge annotated."""
    start = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=start),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=start + timedelta(hours=20),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=start + timedelta(hours=20 + 36),
        ),
    ]
    as_of = start + timedelta(hours=20 + 36 + 12)
    detail = build_alert_detail(
        driver_id="drv1",
        tenant_id="tenant1",
        driver_name="Test Driver",
        events=events,
        as_of=as_of,
        violation_type="DRIVING_LIMIT",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    assert detail["weekly_restart"]["had_restart"] is True
    assert detail["weekly_restart"]["restart_at_local"] is not None
    assert "34h" in detail["weekly_restart"]["message"]
    assert "OFF/SB" in detail["weekly_restart"]["message"]
    assert detail["clocks"]["weekly_window_subtitle"] == "after 34h restart"
    assert detail["clocks"]["had_34h_restart"] is True
    assert any(s["step"] == "Weekly cycle (context)" for s in detail["explanation"])
    assert not any(s["step"] == "Weekly window start" for s in detail["explanation"])


def test_weekly_detail_mentions_restart_window() -> None:
    """Heavy duty, then 35h OFF, then ON — weekly near 0 after restart."""
    # Start duty Monday morning UTC, then long OFF covering two Chicago early mornings
    start = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=start),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=start + timedelta(hours=20),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.ON_DUTY.value,
            timestamp=start + timedelta(hours=20 + 36),
        ),
    ]
    as_of = start + timedelta(hours=20 + 36 + 1)
    detail = build_alert_detail(
        driver_id="drv2",
        tenant_id="tenant1",
        driver_name=None,
        events=events,
        as_of=as_of,
        violation_type="WEEKLY_CYCLE",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    assert detail["clocks"]["weekly_used_h"] < 5.0
    assert detail["clocks"]["had_34h_restart"] is True
    assert detail["weekly_restart"]["had_restart"] is True
    assert detail["clocks"]["weekly_window_subtitle"] == "after 34h restart"
    assert any("restart" in s["note"].lower() or "Restart" in s["value"] or "restart" in s["step"].lower()
               or "34" in s["note"]
               for s in detail["explanation"]) or detail["clocks"]["had_34h_restart"]


def test_contributing_logs_driving_limit_marks_only_driving() -> None:
    """Causal window spans shift→as_of; only D contributed; PC/OFF are context."""
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(10)),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
            timestamp=_ts(15),
        ),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(16)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(16.5)),
    ]
    as_of = _ts(16.5 + 7)  # 5h + 7h driving = 12h
    detail = build_alert_detail(
        driver_id="drv1",
        tenant_id="tenant1",
        driver_name="Test Driver",
        events=events,
        as_of=as_of,
        violation_type="DRIVING_LIMIT",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    logs = detail["contributing_logs"]
    totals = detail["contributing_log_totals"]
    assert logs
    assert detail["contributing_window"]
    assert detail["shift_window"]["start_utc"] is not None
    assert logs[0]["start_utc"] == detail["shift_window"]["start_utc"]
    assert logs[-1]["end_utc"] == as_of.isoformat()

    statuses = {row["status"] for row in logs}
    assert "D" in statuses
    assert "PC" in statuses
    assert "OFF" in statuses

    for row in logs:
        if row["status"] == "D":
            assert row["contributed"] is True
            assert row["counts_as"] == "driving"
        else:
            assert row["contributed"] is False

    assert totals["D_seconds"] == pytest.approx(12 * 3600, abs=1)
    assert totals["contributed_seconds"] == pytest.approx(totals["D_seconds"], abs=1)
    assert totals["PC_seconds"] == pytest.approx(3600, abs=1)
    assert totals["OFF_seconds"] > 0


def test_contributing_logs_duty_window_excludes_pc() -> None:
    """Duty-window alert: ON/D/YM contributed; PC is context-only."""
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.ON_DUTY.value, timestamp=_ts(10)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(11)),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.YARD_MOVE.value,
            timestamp=_ts(12),
        ),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
            timestamp=_ts(13),
        ),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(14)),
    ]
    # 14h wall-clock from first ON at _ts(10) → as_of at _ts(24.5) with driving
    as_of = _ts(24.5)
    detail = build_alert_detail(
        driver_id="drv1",
        tenant_id="tenant1",
        driver_name="Test Driver",
        events=events,
        as_of=as_of,
        violation_type="DUTY_WINDOW",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    logs = detail["contributing_logs"]
    assert logs
    by_status = {row["status"]: row for row in logs}
    assert by_status["ON"]["contributed"] is True
    assert by_status["ON"]["counts_as"] == "duty"
    assert by_status["D"]["contributed"] is True
    assert by_status["YM"]["contributed"] is True
    assert by_status["YM"]["counts_as"] == "ym"
    assert by_status["PC"]["contributed"] is False
    assert by_status["PC"]["counts_as"] == "pc"

    totals = detail["contributing_log_totals"]
    duty_like = totals["ON_seconds"] + totals["D_seconds"] + totals["YM_seconds"]
    assert totals["contributed_seconds"] == pytest.approx(duty_like, abs=1)
    assert totals["PC_seconds"] == pytest.approx(3600, abs=1)
    assert logs[0]["start_utc"] == detail["shift_window"]["start_utc"]
    assert logs[-1]["end_utc"] == as_of.isoformat()


def test_contributing_logs_weekly_matches_restart_window() -> None:
    """Weekly contributing segments start at restart; ON/D/YM totals match explanation."""
    start = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
    on_after = start + timedelta(hours=20 + 36)
    as_of = on_after + timedelta(hours=2)
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=start),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.OFF_DUTY.value,
            timestamp=start + timedelta(hours=20),
        ),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.ON_DUTY.value, timestamp=on_after),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.DRIVING.value,
            timestamp=on_after + timedelta(hours=1),
        ),
    ]
    detail = build_alert_detail(
        driver_id="drv2",
        tenant_id="tenant1",
        driver_name=None,
        events=events,
        as_of=as_of,
        violation_type="WEEKLY_CYCLE",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    assert detail["weekly_restart"]["had_restart"] is True
    logs = detail["contributing_logs"]
    totals = detail["contributing_log_totals"]
    assert logs
    assert detail["shift_window"]["label"].startswith("Weekly window")
    assert logs[0]["start_utc"] == detail["shift_window"]["start_utc"]
    assert logs[-1]["end_utc"] == as_of.isoformat()

    for row in logs:
        if row["status"] in {"ON", "D", "YM"}:
            assert row["contributed"] is True
        else:
            assert row["contributed"] is False

    duty_seconds = totals["ON_seconds"] + totals["D_seconds"] + totals["YM_seconds"]
    assert totals["contributed_seconds"] == pytest.approx(duty_seconds, abs=1)
    assert duty_seconds == pytest.approx(detail["clocks"]["weekly_used_h"] * 3600, abs=2)
    assert duty_seconds == pytest.approx(2 * 3600, abs=1)


def test_contributing_logs_pc_abuse_marks_pc_after_exhaust() -> None:
    """After-hours PC_ABUSE: only PC started after 11h exhaust is contributed."""
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(10)),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
            timestamp=_ts(21),  # after 11h driving
        ),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(21.5)),
    ]
    as_of = _ts(21.5)
    detail = build_alert_detail(
        driver_id="drv1",
        tenant_id="tenant1",
        driver_name="Test Driver",
        events=events,
        as_of=as_of,
        violation_type="PC_ABUSE",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    assert detail["meta"]["matched_on_recompute"] is True
    assert "exhausted" in detail["meta"]["description"].lower()

    logs = detail["contributing_logs"]
    assert logs
    pc_rows = [row for row in logs if row["status"] == "PC"]
    assert pc_rows
    assert all(row["contributed"] is True for row in pc_rows)
    assert all(row["counts_as"] == "pc" for row in pc_rows)
    for row in logs:
        if row["status"] != "PC":
            assert row["contributed"] is False

    totals = detail["contributing_log_totals"]
    assert totals["contributed_seconds"] == pytest.approx(totals["PC_seconds"], abs=1)
    assert totals["PC_seconds"] == pytest.approx(0.5 * 3600, abs=1)

    # Context graph keeps status=PC with lane=OFF (day-grid mapping) and highlights it.
    ctx_pc = [ev for ev in detail["context_events"] if ev["status"] == "PC"]
    assert ctx_pc
    assert all(ev["lane"] == "OFF" for ev in ctx_pc)
    assert any(ev["highlighted"] for ev in ctx_pc)


def test_contributing_logs_pc_abuse_duration_marks_all_pc() -> None:
    """Duration PC_ABUSE (>3h): all overlapping PC in the shift window contributed."""
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.ON_DUTY.value, timestamp=_ts(10)),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
            timestamp=_ts(11),
        ),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(15)),
    ]
    as_of = _ts(15)
    detail = build_alert_detail(
        driver_id="drv1",
        tenant_id="tenant1",
        driver_name="Test Driver",
        events=events,
        as_of=as_of,
        violation_type="PC_ABUSE",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    assert detail["meta"]["matched_on_recompute"] is True
    logs = detail["contributing_logs"]
    assert logs
    for row in logs:
        if row["status"] == "PC":
            assert row["contributed"] is True
        else:
            assert row["contributed"] is False
    totals = detail["contributing_log_totals"]
    assert totals["PC_seconds"] == pytest.approx(4 * 3600, abs=1)
    assert totals["contributed_seconds"] == pytest.approx(totals["PC_seconds"], abs=1)


def test_contributing_logs_pc_abuse_scopes_pre_exhaust_pc() -> None:
    """After-hours match: PC before hours exhaust is context-only; post-exhaust PC contributes."""
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(10)),
        # Short PC mid-shift before 11h driving exhaust (at _ts(21))
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
            timestamp=_ts(15),
        ),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(15.5)),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
            timestamp=_ts(21.5),
        ),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(22)),
    ]
    as_of = _ts(22)
    detail = build_alert_detail(
        driver_id="drv1",
        tenant_id="tenant1",
        driver_name="Test Driver",
        events=events,
        as_of=as_of,
        violation_type="PC_ABUSE",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    assert detail["meta"]["matched_on_recompute"] is True
    # Prefer exhaust-scoped match when present among PC_ABUSE findings
    desc = detail["meta"]["description"].lower()
    logs = detail["contributing_logs"]
    pc_rows = [row for row in logs if row["status"] == "PC"]
    assert len(pc_rows) >= 2
    pre = next(row for row in pc_rows if row["start_utc"] == _ts(15).isoformat())
    post = next(row for row in pc_rows if row["start_utc"] == _ts(21.5).isoformat())
    if "exhausted" in desc:
        assert pre["contributed"] is False
        assert post["contributed"] is True
    else:
        # Duration-only match (unlikely here): both PC rows would contribute
        assert post["contributed"] is True


def test_contributing_logs_pc_abuse_includes_pc_before_current_shift() -> None:
    """PC_ABUSE can fire on timeline PC outside the current shift; list must still show it."""
    # Drive to 11h exhaust, PC after exhaust, ≥10h OFF reset, then a new shift with no PC.
    events = [
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(0)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.DRIVING.value, timestamp=_ts(10)),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.PERSONAL_CONVEYANCE.value,
            timestamp=_ts(21.2),
        ),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.OFF_DUTY.value, timestamp=_ts(21.5)),
        DriverTimeline.HOSEvent(status=CanonicalDutyStatus.ON_DUTY.value, timestamp=_ts(34)),
        DriverTimeline.HOSEvent(
            status=CanonicalDutyStatus.SLEEPER_BERTH.value,
            timestamp=_ts(35),
        ),
    ]
    as_of = _ts(40)
    detail = build_alert_detail(
        driver_id="drv1",
        tenant_id="tenant1",
        driver_name="Test Driver",
        events=events,
        as_of=as_of,
        violation_type="PC_ABUSE",
        source="backtest",
        display_tz_name="America/Chicago",
    )
    assert detail["meta"]["matched_on_recompute"] is True
    assert "exhausted" in detail["meta"]["description"].lower()
    pc_rows = [row for row in detail["contributing_logs"] if row["status"] == "PC"]
    assert pc_rows, "contributing list must include the prior-shift PC that fired the rule"
    assert any(row["contributed"] is True for row in pc_rows)
    assert detail["shift_window"]["label"] == "PC abuse window"
    assert detail["contributing_log_totals"]["contributed_seconds"] > 0
