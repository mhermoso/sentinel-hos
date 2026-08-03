"""Unit tests for alert filter normalization and date windows."""

from __future__ import annotations

from pathlib import Path

from app.domains.dashboard.alert_filters import (
    default_alerts_local_range,
    detect_active_range,
    normalize_filter_str,
    quick_range_dates,
)
from app.domains.dashboard.day_builder import collect_fleet_alerts
from app.domains.notifier.alert_logger import read_alert_log


def test_normalize_empty_source_and_all() -> None:
    assert normalize_filter_str("") is None
    assert normalize_filter_str("   ") is None
    assert normalize_filter_str("all") is None
    assert normalize_filter_str("ALL") is None
    assert normalize_filter_str("backtest") == "backtest"


def test_collect_fleet_alerts_empty_source_means_both() -> None:
    as_of = "2026-07-15T12:00:00+00:00"
    backtest = [
        {
            "as_of": as_of,
            "driver_id": "d1",
            "violation_type": "DRIVING_LIMIT",
            "severity": "WARNING",
            "description": "bt",
        }
    ]
    live = [
        {
            "as_of": as_of,
            "driver_id": "d2",
            "violation_type": "DUTY_WINDOW",
            "severity": "VIOLATION",
            "source": "live_audit",
            "description": "live",
        }
    ]
    merged = collect_fleet_alerts(backtest, live, source="")
    assert len(merged) == 2
    merged_none = collect_fleet_alerts(backtest, live, source=None)
    assert len(merged_none) == 2


def test_collect_fleet_alerts_keeps_same_rule_for_different_drivers() -> None:
    """Fleet merge must not drop another driver's identical rule/severity/minute."""
    as_of = "2026-07-15T12:00:30+00:00"
    live = [
        {
            "as_of": as_of,
            "driver_id": "driver-a",
            "violation_type": "WEEKLY_CYCLE",
            "severity": "WARNING",
            "source": "live_audit",
            "description": "A approaching weekly limit",
        },
        {
            "as_of": as_of,
            "driver_id": "driver-b",
            "violation_type": "WEEKLY_CYCLE",
            "severity": "WARNING",
            "source": "live_audit",
            "description": "B approaching weekly limit",
        },
    ]
    merged = collect_fleet_alerts([], live)
    assert len(merged) == 2
    assert {m["driver_id"] for m in merged} == {"driver-a", "driver-b"}


def test_quick_ranges_and_default_30d() -> None:
    tz = "America/Chicago"
    start, end = default_alerts_local_range(tz)
    assert (end - start).days == 29
    assert detect_active_range(start, end, tz) == "30d"
    s7, e7 = quick_range_dates("7d", tz)
    assert (e7 - s7).days == 6
    assert detect_active_range(s7, e7, tz) == "7d"
    s21, e21 = quick_range_dates("21d", tz)
    assert (e21 - s21).days == 20
    cur_s, cur_e = quick_range_dates("current_month", tz)
    assert cur_s.day == 1
    assert cur_e == end
    last_s, last_e = quick_range_dates("last_month", tz)
    assert last_s.day == 1
    assert last_e < cur_s


def test_read_alert_log_newest_first(tmp_path: Path) -> None:
    path = tmp_path / "alerts.log"
    path.write_text(
        '{"timestamp":"t1","driver_id":"a","dispatch_action":"skipped_dry_run_voice"}\n'
        '{"timestamp":"t2","driver_id":"b","dispatch_action":"suppressed"}\n'
        "not-json\n"
        '{"timestamp":"t3","driver_id":"c","dispatch_action":"sent"}\n',
        encoding="utf-8",
    )
    rows = read_alert_log(limit=2, path=path)
    assert len(rows) == 2
    assert rows[0]["driver_id"] == "c"
    assert rows[1]["driver_id"] == "b"
