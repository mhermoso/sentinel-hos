"""Build a display-timezone day view of HOS status for the dashboard UI.

Day boundaries use the caller's display timezone (default ``America/Chicago``).

Geotab DutyStatusLog includes non-status events (``MotionStopped``, ``Certify``,
``DrivingWhileInExemption``, etc.) that our mapper stores as ``UNKNOWN``, plus
ignored / inactive records (``isIgnored`` / ``eventRecordStatus`` 2–4). Those
must **not** interrupt the duty line — MyGeotab keeps the previous OFF/SB/D/ON/PC/YM
status. This builder skips them when building the timeline so durations match
Geotab totals.

PC plots on the OFF lane (striped); YM plots on the ON lane (striped).

Distance uses odometer deltas on DutyStatusLog. The persisted field
``odometer_km`` holds **meters** (Geotab units; legacy field name).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.redis import get_redis
from app.domains.engine.backtest_runner import backtest_dispatches_key
from app.domains.ingestion.duty_filter import should_skip_duty_status_change

logger = logging.getLogger("dcw.dashboard.day_builder")

# Duty lanes (+ UNKNOWN only if carry-in has no other status). Totals sum these.
GRID_STATUSES: tuple[str, ...] = ("OFF", "SB", "D", "ON", "UNKNOWN")
EXEMPTION_STATUSES: tuple[str, ...] = ("PC", "YM")
# Status → Y-lane for the Geotab-style grid (PC→OFF, YM→ON)
LANE_FOR_STATUS: dict[str, str] = {
    "OFF": "OFF",
    "SB": "SB",
    "D": "D",
    "ON": "ON",
    "UNKNOWN": "UNKNOWN",
    "PC": "OFF",
    "YM": "ON",
}
PLOT_STATUSES = frozenset(LANE_FOR_STATUS)
METERS_PER_MILE = 1609.344
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BACKTEST_DISPATCHES_PATH = _BACKEND_ROOT / "data" / "backtest_dispatches.json"


@dataclass(frozen=True)
class DayBounds:
    """UTC window for a local calendar day."""

    local_date: date
    timezone: str
    start_utc: datetime
    end_utc: datetime


@dataclass(frozen=True)
class RawHOSEvent:
    """Minimal event fields needed for day construction."""

    status: str
    event_timestamp: datetime
    raw_id: str = ""
    device_id: str | None = None
    annotation: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    # Geotab odometer in meters (stored historically as odometer_km)
    odometer_m: float | None = None
    raw_payload: Mapping[str, Any] | None = field(default=None, hash=False)


@dataclass
class _TimelinePoint:
    ts: datetime
    status: str
    odometer_m: float | None
    origin: str = ""
    annotation: str | None = None
    device_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    continued: bool = False


def home_terminal_tz() -> ZoneInfo:
    return ZoneInfo(settings.DEFAULT_HOME_TERMINAL_TIMEZONE)


def chicago_day_bounds(local_date: date, tz: ZoneInfo | None = None) -> DayBounds:
    """Convert a local calendar date to a half-open UTC window ``[00:00, 24:00)``."""
    zone = tz or home_terminal_tz()
    start_local = datetime(local_date.year, local_date.month, local_date.day, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return DayBounds(
        local_date=local_date,
        timezone=str(zone),
        start_utc=start_local.astimezone(UTC),
        end_utc=end_local.astimezone(UTC),
    )


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def format_duration_hhmm(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    return f"{hours:02d}:{minutes:02d}"


def format_duration_hms(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}h {minutes}m {secs}s"


def format_distance_mi(meters: float) -> str:
    miles = max(0.0, meters) / METERS_PER_MILE
    return f"{miles:.1f} mi"


def format_distance_km(meters: float) -> str:
    return f"{max(0.0, meters) / 1000.0:.1f} km"


def _is_non_duty(event: RawHOSEvent) -> bool:
    return should_skip_duty_status_change(event.status, event.raw_payload)


def _event_origin(event: RawHOSEvent) -> str:
    payload = event.raw_payload or {}
    origin = payload.get("origin")
    return str(origin) if origin else ""


def _location_label(
    event: RawHOSEvent | None,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
) -> str:
    """Best-effort location string from Geotab payload or lat/lon."""
    payload = (event.raw_payload if event else None) or {}
    loc = payload.get("location")
    if isinstance(loc, dict):
        for key in ("address", "formattedAddress", "name", "description"):
            val = loc.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        nested = loc.get("location")
        if isinstance(nested, dict):
            for key in ("address", "formattedAddress", "name"):
                val = nested.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    lat = latitude if latitude is not None else (event.latitude if event else None)
    lon = longitude if longitude is not None else (event.longitude if event else None)
    if lat is not None and lon is not None and not (lat == 0.0 and lon == 0.0):
        return f"{lat:.4f}, {lon:.4f}"
    return ""


def compute_status_totals(
    points: Sequence[tuple[datetime, str, float | None]],
    day_end: datetime,
) -> dict[str, float]:
    """Accumulate seconds and odometer meters per GRID status until day_end.

    Each point is ``(timestamp, status, odometer_m_at_start)``. Distance for a
    segment is ``max(0, odo_next - odo_curr)`` when both ends have odometer.

    PC folds into OFF (and ``exemption_pc_*``); YM folds into ON
    (and ``exemption_ym_*``).
    """
    totals = {status: 0.0 for status in GRID_STATUSES}
    totals["exemption_pc_seconds"] = 0.0
    totals["exemption_ym_seconds"] = 0.0
    for key in (
        "distance_m",
        "OFF_m",
        "SB_m",
        "D_m",
        "ON_m",
        "UNKNOWN_m",
        "exemption_pc_m",
        "exemption_ym_m",
    ):
        totals[key] = 0.0
    if not points:
        return totals

    for idx, (ts, status, odo) in enumerate(points):
        next_ts = points[idx + 1][0] if idx + 1 < len(points) else day_end
        next_odo = points[idx + 1][2] if idx + 1 < len(points) else None
        if next_ts <= ts:
            continue
        duration = (next_ts - ts).total_seconds()
        dist = 0.0
        if odo is not None and next_odo is not None and next_odo >= odo:
            dist = next_odo - odo

        if status == "PC":
            totals["OFF"] += duration
            totals["exemption_pc_seconds"] += duration
            totals["OFF_m"] += dist
            totals["exemption_pc_m"] += dist
        elif status == "YM":
            totals["ON"] += duration
            totals["exemption_ym_seconds"] += duration
            totals["ON_m"] += dist
            totals["exemption_ym_m"] += dist
        elif status in GRID_STATUSES:
            totals[status] += duration
            totals[f"{status}_m"] += dist
        totals["distance_m"] += dist
    return totals


def _clip_continued_odometer_to_day_start(
    timeline: list[_TimelinePoint],
    carry_event: RawHOSEvent | None,
) -> None:
    """Interpolate odometer at local midnight for carry-forward segments.

    Without this, ``next_odo - carry_odo`` spans the full pre-day→next interval
    while the row duration is only ``[day_start, next)``.
    """
    if (
        not timeline
        or not timeline[0].continued
        or carry_event is None
        or len(timeline) < 2
    ):
        return
    point = timeline[0]
    nxt = timeline[1]
    carry_odo = point.odometer_m
    next_odo = nxt.odometer_m
    if carry_odo is None or next_odo is None or next_odo < carry_odo:
        return
    carry_ts = _ensure_utc(carry_event.event_timestamp)
    next_ts = nxt.ts
    span = (next_ts - carry_ts).total_seconds()
    if span <= 0:
        return
    into = (point.ts - carry_ts).total_seconds()
    if into <= 0:
        return
    if into >= span:
        point.odometer_m = next_odo
        return
    point.odometer_m = carry_odo + (next_odo - carry_odo) * (into / span)


def _timeline_point_from_event(
    ts: datetime,
    event: RawHOSEvent,
    *,
    odometer_m: float | None = None,
    continued: bool = False,
) -> _TimelinePoint:
    return _TimelinePoint(
        ts=ts,
        status=event.status,
        odometer_m=odometer_m if odometer_m is not None else event.odometer_m,
        origin=_event_origin(event),
        annotation=event.annotation,
        device_id=event.device_id,
        latitude=event.latitude,
        longitude=event.longitude,
        continued=continued,
    )


def build_day_points(
    events: Sequence[RawHOSEvent],
    bounds: DayBounds,
) -> tuple[list[dict[str, Any]], dict[str, float], str | None]:
    """Build clipped day status points with carry-forward and duration totals.

    Returns ``(grid_events, totals_seconds, carry_forward_status)``.

    UNKNOWN / ignored / inactive Geotab logs do not interrupt the previous duty
    status — matching MyGeotab. PC/YM use lanes OFF/ON for stripes.
    """
    start = bounds.start_utc
    end = bounds.end_utc

    sorted_events = sorted(events, key=lambda e: _ensure_utc(e.event_timestamp))
    carry_any: str | None = None
    carry_duty: str | None = None
    carry_odo: float | None = None
    carry_event: RawHOSEvent | None = None
    day_events: list[RawHOSEvent] = []

    for event in sorted_events:
        ts = _ensure_utc(event.event_timestamp)
        if ts < start:
            carry_any = event.status
            if not _is_non_duty(event):
                carry_duty = event.status
                carry_event = event
                if event.odometer_m is not None:
                    carry_odo = event.odometer_m
        elif ts < end:
            day_events.append(event)

    # Prefer last real duty status before the day (skip trailing UNKNOWN noise)
    carry = carry_duty if carry_duty is not None else carry_any

    timeline: list[_TimelinePoint] = []
    if carry is not None:
        if carry_event is not None and carry_event.status == carry:
            timeline.append(
                _timeline_point_from_event(
                    start, carry_event, odometer_m=carry_odo, continued=True
                )
            )
        else:
            timeline.append(
                _TimelinePoint(
                    ts=start,
                    status=carry,
                    odometer_m=carry_odo,
                    continued=True,
                )
            )
    for event in day_events:
        if _is_non_duty(event):
            continue
        ts = _ensure_utc(event.event_timestamp)
        odo = event.odometer_m
        if timeline and timeline[-1].ts == ts:
            prev = timeline[-1]
            timeline[-1] = _timeline_point_from_event(
                ts,
                event,
                odometer_m=odo if odo is not None else prev.odometer_m,
                continued=False,
            )
        else:
            if odo is None and timeline:
                odo = timeline[-1].odometer_m
            timeline.append(
                _timeline_point_from_event(ts, event, odometer_m=odo, continued=False)
            )

    # Carry-forward points reuse the pre-midnight odometer sample. Clip that
    # reading to the day boundary so overnight miles are not attributed to the
    # short post-midnight "(Continued)" slice (e.g. 68 mi in 3 minutes).
    _clip_continued_odometer_to_day_start(timeline, carry_event)

    totals = compute_status_totals(
        [(p.ts, p.status, p.odometer_m) for p in timeline],
        end,
    )

    grid_events: list[dict[str, Any]] = []
    for idx, point in enumerate(timeline):
        next_ts = timeline[idx + 1].ts if idx + 1 < len(timeline) else end
        next_odo = timeline[idx + 1].odometer_m if idx + 1 < len(timeline) else None
        duration = max(0.0, (next_ts - point.ts).total_seconds())
        dist_m = 0.0
        if point.odometer_m is not None and next_odo is not None and next_odo >= point.odometer_m:
            dist_m = next_odo - point.odometer_m
        lane = LANE_FOR_STATUS.get(point.status)
        if lane is None:
            continue
        local_ts = point.ts.astimezone(ZoneInfo(bounds.timezone))
        local_end = next_ts.astimezone(ZoneInfo(bounds.timezone))
        hour_of_day = (
            local_ts.hour
            + local_ts.minute / 60.0
            + local_ts.second / 3600.0
            + local_ts.microsecond / 3_600_000_000.0
        )
        grid_events.append(
            {
                "status": point.status,
                "lane": lane,
                "event_timestamp": point.ts,
                "local_timestamp": local_ts.isoformat(),
                "local_end_timestamp": local_end.isoformat(),
                "hour_of_day": hour_of_day,
                "duration_seconds": duration,
                "duration_hhmm": format_duration_hhmm(duration),
                "duration_hms": format_duration_hms(duration),
                "distance_m": dist_m,
                "distance_mi": round(dist_m / METERS_PER_MILE, 2),
                "distance_km": round(dist_m / 1000.0, 2),
                "distance_label": format_distance_mi(dist_m) if dist_m > 0 else "",
                "origin": point.origin,
                "annotation": point.annotation,
                "device_id": point.device_id,
                "latitude": point.latitude,
                "longitude": point.longitude,
                "location_label": _location_label(
                    None,
                    latitude=point.latitude,
                    longitude=point.longitude,
                ),
                "continued": point.continued,
                "alerts": [],
            }
        )

    # Enrich from source events (payload address beats bare lat/lon)
    event_by_ts: dict[datetime, RawHOSEvent] = {}
    for event in day_events:
        if _is_non_duty(event):
            continue
        event_by_ts[_ensure_utc(event.event_timestamp)] = event
    if carry_event is not None and carry_event.status == carry:
        event_by_ts[start] = carry_event

    for ev in grid_events:
        src = event_by_ts.get(_ensure_utc(ev["event_timestamp"]))
        if src is None:
            continue
        ev["origin"] = _event_origin(src) or ev.get("origin") or ""
        if src.annotation is not None:
            ev["annotation"] = src.annotation
        if src.device_id is not None:
            ev["device_id"] = src.device_id
        if src.latitude is not None:
            ev["latitude"] = src.latitude
        if src.longitude is not None:
            ev["longitude"] = src.longitude
        label = _location_label(src)
        if label:
            ev["location_label"] = label

    return grid_events, totals, carry


def attach_alerts_to_segments(
    grid_events: list[dict[str, Any]],
    markers: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach markers whose ``as_of`` falls in ``[segment_start, segment_end)``."""
    for ev in grid_events:
        start = _ensure_utc(ev["event_timestamp"])
        end = start + timedelta(seconds=float(ev.get("duration_seconds", 0.0)))
        alerts: list[dict[str, Any]] = []
        for marker in markers:
            as_of = _ensure_utc(marker["as_of"])
            if start <= as_of < end:
                alerts.append(
                    {
                        "as_of": as_of,
                        "violation_type": str(marker.get("violation_type", "")),
                        "severity": str(marker.get("severity", "")),
                        "rule_ref": str(marker.get("rule_ref", "")),
                        "description": str(marker.get("description", "")),
                        "source": str(marker.get("source", "")),
                    }
                )
        ev["alerts"] = alerts
    return grid_events


def _rows_from_dispatch_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("dispatches") or payload.get("dispatch_events") or []
    return list(rows) if isinstance(rows, list) else []


async def load_backtest_dispatches(
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Load would-dispatch markers from Redis (online) or local JSON (dev fallback).

    Backtest dispatches remain Geotab-fleet-only (engine seed / runner scoped).
    """
    tenant_id = settings.GEOTAB_DATABASE
    if tenant_id:
        try:
            redis = await get_redis()
            raw = await redis.get(backtest_dispatches_key(tenant_id))
            if raw:
                payload = json.loads(raw)
                rows = _rows_from_dispatch_payload(payload)
                if rows:
                    return rows
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Failed to load backtest dispatches from Redis (%s): %s",
                backtest_dispatches_key(tenant_id),
                exc,
            )

    dispatch_path = path or DEFAULT_BACKTEST_DISPATCHES_PATH
    if not dispatch_path.exists():
        return []
    try:
        with dispatch_path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load backtest dispatches from %s: %s", dispatch_path, exc)
        return []
    return _rows_from_dispatch_payload(payload)


def _parse_iso_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        return _ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def filter_backtest_markers(
    dispatches: Iterable[dict[str, Any]],
    driver_id: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for row in dispatches:
        if str(row.get("driver_id", "")) != driver_id:
            continue
        as_of = _parse_iso_dt(row.get("as_of"))
        if as_of is None or as_of < start_utc or as_of >= end_utc:
            continue
        markers.append(
            {
                "as_of": as_of,
                "violation_type": str(row.get("violation_type", "")),
                "severity": str(row.get("severity", "")),
                "rule_ref": str(row.get("rule_ref", "")),
                "description": str(row.get("description", "")),
                "source": "backtest",
                "driver_id": driver_id,
                "driver_name": row.get("driver_name"),
            }
        )
    return markers


def markers_from_audit_violations(
    violations: Iterable[dict[str, Any]],
    start_utc: datetime,
    end_utc: datetime,
    *,
    driver_id: str = "",
) -> list[dict[str, Any]]:
    """Build live markers, collapsing sweeper duplicates of the same rule/severity."""
    markers: list[dict[str, Any]] = []
    for violation in violations:
        detected = _parse_iso_dt(violation.get("detected_at"))
        if detected is None or detected < start_utc or detected >= end_utc:
            continue
        markers.append(
            {
                "as_of": detected,
                "violation_type": str(violation.get("violation_type", "")),
                "severity": str(violation.get("severity", "")),
                "rule_ref": str(violation.get("rule_ref", "")),
                "description": str(violation.get("description", "")),
                "source": "live_audit",
                "driver_id": driver_id or str(violation.get("driver_id", "")),
                "driver_name": violation.get("driver_name"),
            }
        )
    # Keep first occurrence per (type, severity) for the day — sweeper re-publishes often
    markers.sort(key=lambda m: _ensure_utc(m["as_of"]))
    collapsed: list[dict[str, Any]] = []
    seen_rule: set[tuple[str, str]] = set()
    for marker in markers:
        key = (str(marker["violation_type"]), str(marker["severity"]))
        if key in seen_rule:
            continue
        seen_rule.add(key)
        collapsed.append(marker)
    return collapsed


def merge_alert_markers(
    *marker_groups: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate markers by (type, severity, source, minute bucket) and sort."""
    seen: set[tuple[str, str, str, str]] = set()
    merged: list[dict[str, Any]] = []
    for group in marker_groups:
        for marker in group:
            as_of = _ensure_utc(marker["as_of"])
            minute_bucket = as_of.replace(second=0, microsecond=0).isoformat()
            key = (
                minute_bucket,
                str(marker.get("violation_type", "")),
                str(marker.get("severity", "")),
                str(marker.get("source", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(marker)
    merged.sort(key=lambda m: _ensure_utc(m["as_of"]))
    return merged


def annotate_marker_hours(
    markers: Sequence[dict[str, Any]],
    tz_name: str,
) -> list[dict[str, Any]]:
    zone = ZoneInfo(tz_name)
    annotated: list[dict[str, Any]] = []
    for marker in markers:
        as_of = _ensure_utc(marker["as_of"])
        local_ts = as_of.astimezone(zone)
        hour_of_day = (
            local_ts.hour
            + local_ts.minute / 60.0
            + local_ts.second / 3600.0
        )
        annotated.append(
            {
                **marker,
                "as_of": as_of,
                "local_timestamp": local_ts.isoformat(),
                "hour_of_day": hour_of_day,
            }
        )
    return annotated


def collect_fleet_alerts(
    dispatches: Iterable[dict[str, Any]],
    live_markers: Iterable[dict[str, Any]],
    *,
    severity: str | None = None,
    driver_id: str | None = None,
    source: str | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """Merge backtest + live markers for the fleet Alerts tab / API."""
    backtest_rows: list[dict[str, Any]] = []
    for row in dispatches:
        as_of = _parse_iso_dt(row.get("as_of"))
        if as_of is None:
            continue
        if start_utc is not None and as_of < start_utc:
            continue
        if end_utc is not None and as_of >= end_utc:
            continue
        did = str(row.get("driver_id", ""))
        if driver_id and did != driver_id:
            continue
        backtest_rows.append(
            {
                "as_of": as_of,
                "violation_type": str(row.get("violation_type", "")),
                "severity": str(row.get("severity", "")),
                "rule_ref": str(row.get("rule_ref", "")),
                "description": str(row.get("description", "")),
                "source": "backtest",
                "driver_id": did,
                "driver_name": row.get("driver_name"),
            }
        )

    live_rows: list[dict[str, Any]] = []
    for marker in live_markers:
        as_of = _parse_iso_dt(marker.get("as_of") or marker.get("detected_at"))
        if as_of is None:
            continue
        if start_utc is not None and as_of < start_utc:
            continue
        if end_utc is not None and as_of >= end_utc:
            continue
        did = str(marker.get("driver_id", ""))
        if driver_id and did != driver_id:
            continue
        live_rows.append(
            {
                "as_of": as_of,
                "violation_type": str(marker.get("violation_type", "")),
                "severity": str(marker.get("severity", "")),
                "rule_ref": str(marker.get("rule_ref", "")),
                "description": str(marker.get("description", "")),
                "source": str(marker.get("source", "live_audit")),
                "driver_id": did,
                "driver_name": marker.get("driver_name"),
            }
        )

    merged = merge_alert_markers(backtest_rows, live_rows)
    sev_filter = (severity or "").strip()
    if sev_filter and sev_filter.lower() != "all":
        sev = sev_filter.upper()
        # Legacy UI label "Alert" mapped to CRITICAL
        if sev == "ALERT":
            sev = "CRITICAL"
        merged = [m for m in merged if str(m.get("severity", "")).upper() == sev]
    src_filter = (source or "").strip()
    if src_filter and src_filter.lower() != "all":
        src = src_filter.lower()
        merged = [m for m in merged if str(m.get("source", "")).lower() == src]
    return merged
