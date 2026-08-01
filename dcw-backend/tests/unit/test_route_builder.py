"""Unit tests for driver-day GPS route builder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domains.dashboard.route_builder import (
    build_day_route_payload,
    build_route_segments,
    build_status_lookup,
    downsample_breadcrumbs,
    map_status_for_route,
    place_alert_points,
    status_color,
)


def test_map_status_pc_ym() -> None:
    assert map_status_for_route("PC") == "OFF"
    assert map_status_for_route("YM") == "ON"
    assert map_status_for_route("D") == "D"
    assert map_status_for_route("weird") == "UNKNOWN"


def test_status_color_matches_home_map() -> None:
    assert status_color("D") == "#3d9cf0"
    assert status_color("OFF") == "#8b9aab"
    assert status_color("PC") == "#8b9aab"


def test_downsample_keeps_status_change_and_30s() -> None:
    t0 = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    points = [
        {"event_timestamp": t0 + timedelta(seconds=s), "latitude": 41.0, "longitude": -87.0}
        for s in (0, 10, 20, 35, 40, 70)
    ]
    # Status flips at t0+40
    def status_at(ts: datetime) -> str:
        return "D" if ts < t0 + timedelta(seconds=40) else "ON"

    kept = downsample_breadcrumbs(points, status_at, min_seconds=30)
    times = [p["event_timestamp"] for p in kept]
    assert times[0] == t0
    assert t0 + timedelta(seconds=35) in times  # 30s gap
    assert t0 + timedelta(seconds=40) in times  # status change
    assert times[-1] == t0 + timedelta(seconds=70)


def test_build_route_segments_colored_by_earlier_status() -> None:
    t0 = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
    points = [
        {"event_timestamp": t0, "latitude": 1.0, "longitude": 2.0},
        {"event_timestamp": t0 + timedelta(minutes=5), "latitude": 1.1, "longitude": 2.1},
        {"event_timestamp": t0 + timedelta(minutes=10), "latitude": 1.2, "longitude": 2.2},
    ]
    events = [
        {"event_timestamp": t0 - timedelta(hours=1), "status": "OFF"},
        {"event_timestamp": t0 + timedelta(minutes=5), "status": "D"},
    ]
    status_at = build_status_lookup(events)
    segs = build_route_segments(points, status_at)
    assert len(segs) == 2
    assert segs[0]["status"] == "OFF"
    assert segs[1]["status"] == "D"
    assert segs[1]["color"] == "#3d9cf0"


def test_place_alert_nearest_breadcrumb() -> None:
    t0 = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
    crumbs = [
        {"event_timestamp": t0, "latitude": 41.0, "longitude": -87.0},
        {"event_timestamp": t0 + timedelta(minutes=10), "latitude": 41.1, "longitude": -87.1},
    ]
    markers = [
        {
            "as_of": t0 + timedelta(minutes=9),
            "severity": "WARNING",
            "violation_type": "break",
            "rule_ref": "r1",
            "description": "break soon",
            "source": "backtest",
        }
    ]
    placed = place_alert_points(markers, crumbs, [])
    assert len(placed) == 1
    assert placed[0]["lat"] == 41.1
    assert placed[0]["severity"] == "WARNING"


def test_build_day_route_empty_coverage_note() -> None:
    payload = build_day_route_payload(
        driver_id="d1",
        local_date="2026-07-30",
        breadcrumbs=[],
        hos_events=[],
        alert_markers=[],
    )
    assert payload["meta"]["point_count"] == 0
    assert "unavailable" in payload["meta"]["coverage_note"].lower()
    assert payload["segments"] == []
