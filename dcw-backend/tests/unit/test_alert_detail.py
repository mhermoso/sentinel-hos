"""Unit tests for alert calculation detail + display timezone preference."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

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
