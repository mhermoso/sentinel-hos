"""Normalization functions for raw telematics data.

Implements the Normalization Engine from the architecture spec:
- Truncate sub-second to 1-second precision
- Round GPS to 4 decimal places (~11.1m precision)
- ECM precedence override (off-duty → DRIVING when speed > 5 mph)
- Sanitize raw payloads (mask sensitive keys)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.domains.ingestion.schemas import CanonicalDutyStatus, DCWCanonicalHOSLog


# ── Payload Sanitisation ─────────────────────────────────────────────────

_SENSITIVE_KEYS = {"password", "sessionid", "credentials", "token", "secret", "auth"}


def sanitize_raw_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively scrub sensitive keys from raw payload dicts."""
    sanitized: Dict[str, Any] = {}
    for k, v in payload.items():
        if k.lower() in _SENSITIVE_KEYS:
            sanitized[k] = "[MASKED]"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_raw_payload(v)
        elif isinstance(v, list):
            sanitized[k] = [
                sanitize_raw_payload(item) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            sanitized[k] = v
    return sanitized


# ── Timestamp Normalisation ──────────────────────────────────────────────

def normalize_timestamp(dt: datetime) -> datetime:
    """Truncate sub-second microsecond noise to 1-second precision.

    Per spec: truncates sub-second microsecond noise to 1-second precision.
    """
    return dt.replace(microsecond=0)


# ── GPS Coordinate Normalisation ─────────────────────────────────────────

def normalize_coordinates(
    latitude: Optional[float],
    longitude: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Round GPS coordinates to 4 decimal places (~11.1m precision)."""
    if latitude is not None:
        latitude = round(latitude, 4)
    if longitude is not None:
        longitude = round(longitude, 4)
    return latitude, longitude


# ── ECM Precedence Override ──────────────────────────────────────────────

def apply_ecm_override(
    status: CanonicalDutyStatus,
    vehicle_speed_mph: Optional[float] = None,
) -> CanonicalDutyStatus:
    """Override ambiguous off-duty status to DRIVING if vehicle speed > 5 mph.

    Per spec: ECM precedence overrides off-duty/on-duty to DRIVING when
    the ECM reports vehicle speed exceeding 5 mph.
    """
    if vehicle_speed_mph is not None and vehicle_speed_mph > 5.0:
        if status in (CanonicalDutyStatus.OFF_DUTY, CanonicalDutyStatus.ON_DUTY):
            return CanonicalDutyStatus.DRIVING
    return status


# ── Full Normalisation Pipeline ──────────────────────────────────────────

def normalize_canonical_log(
    log: DCWCanonicalHOSLog,
    vehicle_speed_mph: Optional[float] = None,
) -> DCWCanonicalHOSLog:
    """Apply the full normalisation pipeline to a canonical HOS log.

    1. Truncate timestamp to 1-second precision.
    2. Round GPS to 4 decimal places.
    3. Apply ECM speed override.

    Returns a new (frozen) ``DCWCanonicalHOSLog`` with normalised values.
    """
    normalised_ts = normalize_timestamp(log.event_timestamp)
    normalised_lat, normalised_lon = normalize_coordinates(
        log.latitude, log.longitude
    )
    normalised_status = apply_ecm_override(log.status, vehicle_speed_mph)

    return log.model_copy(
        update={
            "event_timestamp": normalised_ts,
            "latitude": normalised_lat,
            "longitude": normalised_lon,
            "status": normalised_status,
        }
    )


def normalize_batch(
    logs: List[DCWCanonicalHOSLog],
    vehicle_speed_mph: Optional[float] = None,
) -> List[DCWCanonicalHOSLog]:
    """Apply normalisation to a batch of canonical logs."""
    return [normalize_canonical_log(log, vehicle_speed_mph) for log in logs]
