"""Unit tests for home-terminal day grid construction."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.domains.dashboard.day_builder import (
    RawHOSEvent,
    build_day_points,
    chicago_day_bounds,
    filter_backtest_markers,
    format_duration_hhmm,
    merge_alert_markers,
)


def test_chicago_day_bounds_dst_safe() -> None:
    bounds = chicago_day_bounds(date(2025, 7, 28), ZoneInfo("America/Chicago"))
    assert bounds.start_utc == datetime(2025, 7, 28, 5, 0, tzinfo=timezone.utc)
    assert bounds.end_utc == datetime(2025, 7, 29, 5, 0, tzinfo=timezone.utc)


def test_carry_forward_and_unknown_does_not_interrupt() -> None:
    """Geotab non-status logs (UNKNOWN) must not steal time from prior duty."""
    bounds = chicago_day_bounds(date(2025, 7, 28), ZoneInfo("America/Chicago"))
    events = [
        RawHOSEvent("OFF", datetime(2025, 7, 27, 20, 0, tzinfo=timezone.utc)),
        RawHOSEvent("D", datetime(2025, 7, 28, 14, 0, tzinfo=timezone.utc)),  # 9 AM CT
        RawHOSEvent("UNKNOWN", datetime(2025, 7, 28, 16, 0, tzinfo=timezone.utc)),
        RawHOSEvent("ON", datetime(2025, 7, 28, 18, 0, tzinfo=timezone.utc)),
    ]
    grid, totals, carry = build_day_points(events, bounds)
    assert carry == "OFF"
    statuses = [e["status"] for e in grid]
    assert "UNKNOWN" not in statuses
    assert statuses[0] == "OFF"
    assert "D" in statuses
    assert "ON" in statuses
    # UNKNOWN at 11:00 CT is ignored → D continues until ON at 13:00 CT (4h)
    assert totals["D"] == 4 * 3600.0
    assert totals["UNKNOWN"] == 0.0
    tracked = sum(totals[s] for s in ("OFF", "SB", "D", "ON", "UNKNOWN"))
    assert abs(tracked - 86400.0) < 1.0
    assert format_duration_hhmm(3661) == "01:01"


def test_unknown_carry_skipped_when_prior_duty_exists() -> None:
    bounds = chicago_day_bounds(date(2025, 7, 28), ZoneInfo("America/Chicago"))
    events = [
        RawHOSEvent("SB", datetime(2025, 7, 27, 18, 0, tzinfo=timezone.utc)),
        RawHOSEvent("UNKNOWN", datetime(2025, 7, 27, 22, 0, tzinfo=timezone.utc)),
        RawHOSEvent("D", datetime(2025, 7, 28, 15, 0, tzinfo=timezone.utc)),
    ]
    _grid, totals, carry = build_day_points(events, bounds)
    assert carry == "SB"
    assert totals["SB"] == (15 - 5) * 3600.0  # midnight–10:00 CT wait: day start 05:00 UTC = 00:00 CT, D at 15:00 UTC = 10:00 CT → SB 10h
    assert abs(totals["SB"] - 10 * 3600.0) < 1.0


def test_pc_and_ym_emitted_with_lanes_and_totals() -> None:
    bounds = chicago_day_bounds(date(2025, 7, 28), ZoneInfo("America/Chicago"))
    events = [
        RawHOSEvent("PC", datetime(2025, 7, 28, 6, 0, tzinfo=timezone.utc)),  # 1 AM CT
        RawHOSEvent("D", datetime(2025, 7, 28, 12, 0, tzinfo=timezone.utc)),  # 7 AM CT
        RawHOSEvent("YM", datetime(2025, 7, 28, 14, 0, tzinfo=timezone.utc)),  # 9 AM CT
        RawHOSEvent("OFF", datetime(2025, 7, 28, 15, 0, tzinfo=timezone.utc)),
    ]
    grid, totals, _carry = build_day_points(events, bounds)
    by_status = {e["status"]: e for e in grid}
    assert "PC" in by_status
    assert by_status["PC"]["lane"] == "OFF"
    assert "YM" in by_status
    assert by_status["YM"]["lane"] == "ON"
    # PC folds into OFF; YM folds into ON
    assert totals["exemption_pc_seconds"] == 6 * 3600.0
    assert totals["exemption_ym_seconds"] == 1 * 3600.0
    assert totals["OFF"] >= totals["exemption_pc_seconds"]
    assert totals["ON"] >= totals["exemption_ym_seconds"]


def test_odometer_distance_on_segments() -> None:
    bounds = chicago_day_bounds(date(2025, 7, 28), ZoneInfo("America/Chicago"))
    events = [
        RawHOSEvent(
            "D",
            datetime(2025, 7, 28, 12, 0, tzinfo=timezone.utc),
            odometer_m=10_000.0,
        ),
        RawHOSEvent(
            "OFF",
            datetime(2025, 7, 28, 14, 0, tzinfo=timezone.utc),
            odometer_m=26_093.44,  # +10 miles
        ),
    ]
    grid, totals, _carry = build_day_points(events, bounds)
    d_seg = next(e for e in grid if e["status"] == "D")
    assert abs(d_seg["distance_m"] - 16093.44) < 0.01
    assert abs(d_seg["distance_mi"] - 10.0) < 0.01
    assert abs(totals["D_m"] - 16093.44) < 0.01
    assert abs(totals["distance_m"] - 16093.44) < 0.01


def test_merge_alert_markers_dedupes() -> None:
    as_of = datetime(2025, 7, 28, 15, 0, tzinfo=timezone.utc)
    a = {
        "as_of": as_of,
        "violation_type": "DRIVING_LIMIT",
        "severity": "WARNING",
        "source": "backtest",
    }
    b = dict(a)
    merged = merge_alert_markers([a], [b])
    assert len(merged) == 1

    filtered = filter_backtest_markers(
        [
            {
                "driver_id": "d1",
                "as_of": as_of.isoformat(),
                "violation_type": "WEEKLY_CYCLE",
                "severity": "VIOLATION",
                "rule_ref": "§ 395.3(b)",
                "description": "over",
            },
            {
                "driver_id": "other",
                "as_of": as_of.isoformat(),
                "violation_type": "WEEKLY_CYCLE",
                "severity": "VIOLATION",
                "rule_ref": "§ 395.3(b)",
                "description": "skip",
            },
        ],
        "d1",
        datetime(2025, 7, 28, 5, 0, tzinfo=timezone.utc),
        datetime(2025, 7, 29, 5, 0, tzinfo=timezone.utc),
    )
    assert len(filtered) == 1
    assert filtered[0]["violation_type"] == "WEEKLY_CYCLE"
