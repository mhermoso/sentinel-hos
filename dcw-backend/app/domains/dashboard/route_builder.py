"""Build driver-day GPS route segments + alert points from breadcrumbs + HOS.

Used by ``GET /api/drivers/{id}/day/route`` (ADR-007). Pure helpers are
unit-testable without Postgres.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

# Home-map / CSS status colors (dashboard.css :root)
STATUS_COLORS: dict[str, str] = {
    "OFF": "#8b9aab",
    "SB": "#6b8cae",
    "D": "#3d9cf0",
    "ON": "#e6b84d",
    "UNKNOWN": "#6b7280",
}

DOWNSAMPLE_MIN_SECONDS = 30.0


def map_status_for_route(status: str) -> str:
    """Map HOS status to route segment status (PC→OFF, YM→ON)."""
    s = (status or "UNKNOWN").upper()
    if s == "PC":
        return "OFF"
    if s == "YM":
        return "ON"
    if s in STATUS_COLORS:
        return s
    return "UNKNOWN"


def status_color(status: str) -> str:
    """Return hex color for a route status key."""
    return STATUS_COLORS.get(map_status_for_route(status), STATUS_COLORS["UNKNOWN"])


def downsample_breadcrumbs(
    points: Sequence[dict[str, Any]],
    status_at,
    min_seconds: float = DOWNSAMPLE_MIN_SECONDS,
) -> list[dict[str, Any]]:
    """Keep a point if ≥ ``min_seconds`` from previous or HOS status changes.

    ``points`` are dicts with ``event_timestamp``, ``latitude``, ``longitude``.
    ``status_at(ts)`` returns the active HOS status string at timestamp ``ts``.
    """
    if not points:
        return []

    kept: list[dict[str, Any]] = [points[0]]
    last_ts: datetime = points[0]["event_timestamp"]
    last_status = map_status_for_route(status_at(last_ts))

    for pt in points[1:]:
        ts = pt["event_timestamp"]
        st = map_status_for_route(status_at(ts))
        elapsed = (ts - last_ts).total_seconds()
        if elapsed >= min_seconds or st != last_status:
            kept.append(pt)
            last_ts = ts
            last_status = st

    # Always keep the last point for trail completeness
    if points[-1] is not kept[-1]:
        kept.append(points[-1])
    return kept


def build_status_lookup(
    events: Sequence[dict[str, Any]],
    carry_forward_status: str | None = None,
) -> Any:
    """Return ``status_at(ts)`` from a sorted HOS event list.

    Each event needs ``event_timestamp`` and ``status``. Status holds until
    the next event (ZOH). Before the first event, uses ``carry_forward_status``.
    """
    sorted_events = sorted(events, key=lambda e: e["event_timestamp"])

    def status_at(ts: datetime) -> str:
        active = carry_forward_status or "UNKNOWN"
        for ev in sorted_events:
            if ev["event_timestamp"] <= ts:
                active = str(ev["status"])
            else:
                break
        return active

    return status_at


def build_route_segments(
    points: Sequence[dict[str, Any]],
    status_at,
) -> list[dict[str, Any]]:
    """Build consecutive colored segments from downsampled points."""
    segments: list[dict[str, Any]] = []
    if len(points) < 2:
        return segments

    for i in range(len(points) - 1):
        a = points[i]
        b = points[i + 1]
        st = map_status_for_route(status_at(a["event_timestamp"]))
        segments.append(
            {
                "status": st,
                "color": status_color(st),
                "lat1": a["latitude"],
                "lon1": a["longitude"],
                "lat2": b["latitude"],
                "lon2": b["longitude"],
                "t0": a["event_timestamp"],
                "t1": b["event_timestamp"],
            }
        )
    return segments


def nearest_breadcrumb(
    points: Sequence[dict[str, Any]],
    as_of: datetime,
) -> tuple[float, float] | None:
    """Pick lat/lon of breadcrumb closest in time to ``as_of``."""
    if not points:
        return None
    best = min(
        points,
        key=lambda p: abs((p["event_timestamp"] - as_of).total_seconds()),
    )
    return float(best["latitude"]), float(best["longitude"])


def place_alert_points(
    markers: Sequence[dict[str, Any]],
    breadcrumbs: Sequence[dict[str, Any]],
    hos_events: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Place alert markers at nearest breadcrumb, else HOS event lat/lon."""
    out: list[dict[str, Any]] = []
    for m in markers:
        as_of = m["as_of"]
        coords = nearest_breadcrumb(breadcrumbs, as_of)
        if coords is None:
            # Fallback: nearest HOS event with coordinates
            with_coords = [
                e
                for e in hos_events
                if e.get("latitude") is not None and e.get("longitude") is not None
            ]
            if with_coords:
                best = min(
                    with_coords,
                    key=lambda e: abs((e["event_timestamp"] - as_of).total_seconds()),
                )
                coords = (float(best["latitude"]), float(best["longitude"]))
        if coords is None:
            continue
        out.append(
            {
                "as_of": as_of,
                "severity": str(m.get("severity", "")),
                "violation_type": str(m.get("violation_type", "")),
                "rule_ref": str(m.get("rule_ref", "")),
                "description": str(m.get("description", "")),
                "source": str(m.get("source", "")),
                "lat": coords[0],
                "lon": coords[1],
            }
        )
    return out


def build_day_route_payload(
    *,
    driver_id: str = "",
    local_date,
    breadcrumbs: Sequence[dict[str, Any]],
    hos_events: Sequence[dict[str, Any]],
    alert_markers: Sequence[dict[str, Any]],
    carry_forward_status: str | None = None,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Assemble segments + alerts + meta for the day route API.

    Pass ``device_id`` for unit-centric routes (``driver_id`` may be empty).
    """
    status_at = build_status_lookup(hos_events, carry_forward_status)
    downsampled = downsample_breadcrumbs(list(breadcrumbs), status_at)
    segments = build_route_segments(downsampled, status_at)
    alerts = place_alert_points(alert_markers, downsampled or list(breadcrumbs), hos_events)

    point_count = len(breadcrumbs)
    coverage_note = ""
    if point_count == 0:
        coverage_note = (
            "GPS trail unavailable for this day. "
            "Status-only coordinates are not used as a dense route."
        )
    elif point_count < 2:
        coverage_note = "Insufficient GPS points to draw a route."

    return {
        "segments": segments,
        "alerts": alerts,
        "meta": {
            "driver_id": driver_id,
            "device_id": device_id,
            "date": local_date,
            "point_count": point_count,
            "segment_count": len(segments),
            "downsampled_count": len(downsampled),
            "coverage_note": coverage_note,
        },
    }
