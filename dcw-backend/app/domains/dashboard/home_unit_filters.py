"""Home map unit filter predicates and map-payload helpers.

Default Home view: units with a current/last driver and a non-UNKNOWN status.
All GPS units are shipped to the client; toggles filter without reload.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.domains.dashboard.schemas import (
    FleetAlertItemResponse,
    HomeUnitMapItemResponse,
    UnitListItemResponse,
)


def unit_has_usable_gps(
    latitude: float | None,
    longitude: float | None,
    event_timestamp: datetime | None = None,
) -> bool:
    """True when lat/lon (+ optional timestamp) are present and not null-island."""
    if latitude is None or longitude is None:
        return False
    if latitude == 0 and longitude == 0:
        return False
    return event_timestamp is not None


_KNOWN_STATUS_KEYS = frozenset({"OFF", "SB", "D", "ON"})


def legend_status_key(status: str | None) -> str:
    """Map raw HOS status to Home legend group (PC→OFF, YM→ON)."""
    s = (status or "UNKNOWN").strip().upper()
    if s == "PC":
        return "OFF"
    if s == "YM":
        return "ON"
    if s in _KNOWN_STATUS_KEYS or s == "UNKNOWN":
        return s
    return "UNKNOWN"


def has_driver(unit: UnitListItemResponse | HomeUnitMapItemResponse) -> bool:
    """Unit has a resolved current/last driver id."""
    return bool(unit.current_driver_id)


def has_known_status(unit: UnitListItemResponse | HomeUnitMapItemResponse) -> bool:
    """Status maps to a known legend group (not UNKNOWN / missing)."""
    return legend_status_key(unit.current_status) != "UNKNOWN"


def matches_home_unit_filters(
    unit: UnitListItemResponse | HomeUnitMapItemResponse,
    *,
    has_driver_only: bool = False,
    known_status_only: bool = False,
) -> bool:
    """Apply Has driver / Known status gates (Home map toggles)."""
    if has_driver_only and not has_driver(unit):
        return False
    if known_status_only:
        return has_known_status(unit)
    return True


def alert_stats_by_driver(
    alerts: list[FleetAlertItemResponse],
) -> dict[str, tuple[int, int, str | None, str | None]]:
    """Group 30d fleet alerts into (warn, viol, latest_severity, latest_type)."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"WARNING": 0, "VIOLATION": 0})
    latest: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        did = str(alert.driver_id or "")
        if not did:
            continue
        sev = str(alert.severity or "").upper()
        if sev in ("WARNING", "VIOLATION"):
            counts[did][sev] += 1
        as_of = alert.as_of
        prev = latest.get(did)
        if prev is None or (isinstance(as_of, datetime) and as_of > prev["as_of"]):
            latest[did] = {
                "as_of": as_of if isinstance(as_of, datetime) else datetime.min.replace(tzinfo=UTC),
                "severity": str(alert.severity or "") or None,
                "violation_type": str(alert.violation_type or "") or None,
            }
    out: dict[str, tuple[int, int, str | None, str | None]] = {}
    for did, c in counts.items():
        lat = latest.get(did, {})
        out[did] = (
            c["WARNING"],
            c["VIOLATION"],
            lat.get("severity"),
            lat.get("violation_type"),
        )
    for did, lat in latest.items():
        if did not in out:
            out[did] = (0, 0, lat.get("severity"), lat.get("violation_type"))
    return out


def split_units_for_home(
    units: list[UnitListItemResponse],
    alert_stats: dict[str, tuple[int, int, str | None, str | None]] | None = None,
) -> tuple[list[HomeUnitMapItemResponse], list[UnitListItemResponse]]:
    """Split roster units into GPS map items vs no-location rows.

    All units with usable GPS are returned for client-side toggles.
    Alert counts are enriched from ``current_driver_id`` when present.
    Each map item sets ``default_hidden`` when it fails the default Home
    gates (Has driver + Known status).
    """
    stats = alert_stats or {}
    with_gps: list[HomeUnitMapItemResponse] = []
    no_location: list[UnitListItemResponse] = []

    for unit in units:
        if not unit_has_usable_gps(unit.last_gps_lat, unit.last_gps_lon, unit.last_gps_at):
            no_location.append(unit)
            continue
        assert unit.last_gps_lat is not None and unit.last_gps_lon is not None
        assert unit.last_gps_at is not None
        warn, viol, sev, vtype = (0, 0, None, None)
        if unit.current_driver_id:
            warn, viol, sev, vtype = stats.get(unit.current_driver_id, (0, 0, None, None))
        default_visible = matches_home_unit_filters(
            unit, has_driver_only=True, known_status_only=True
        )
        with_gps.append(
            HomeUnitMapItemResponse(
                device_id=unit.device_id,
                name=unit.name,
                current_driver_id=unit.current_driver_id,
                current_driver_name=unit.current_driver_name,
                current_status=unit.current_status,
                latitude=float(unit.last_gps_lat),
                longitude=float(unit.last_gps_lon),
                event_timestamp=unit.last_gps_at,
                warning_count=warn,
                violation_count=viol,
                latest_alert_severity=sev,
                latest_alert_type=vtype,
                default_hidden=not default_visible,
            )
        )

    return with_gps, no_location
