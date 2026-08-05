"""SHA-256 canonical input hasher for audit integrity.

Computes a deterministic SHA-256 digest over the normalised canonical
fields of an HOS log so that every audit record can be tied back to
exactly the inputs that produced it (per ADR-003 / ADR-004).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_inputs_hash(canonical_fields: dict[str, Any]) -> str:
    """Compute a SHA-256 hex digest over a deterministic JSON serialisation.

    Args:
        canonical_fields: Dictionary of normalised canonical log fields.
            Must be JSON-serialisable.  ``datetime`` values should be
            pre-converted to ISO-8601 strings.

    Returns:
        64-character lowercase hex string (SHA-256 digest).
    """
    # ``sort_keys`` guarantees deterministic ordering across Python runs.
    canonical_json = json.dumps(
        canonical_fields,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def hash_canonical_log(log_dict: dict[str, Any]) -> str:
    """Hash the compliance-relevant fields of a canonical HOS log.

    Only fields that affect compliance calculations are included in
    the digest — ``raw_payload`` and ``annotation`` are excluded since
    they are informational metadata and do not influence rule evaluation.
    """
    relevant_fields = {
        "tenant_id": log_dict.get("tenant_id"),
        "driver_id": log_dict.get("driver_id"),
        "raw_id": log_dict.get("raw_id"),
        "status": log_dict.get("status"),
        "event_timestamp": log_dict.get("event_timestamp"),
        "device_id": log_dict.get("device_id"),
        "latitude": log_dict.get("latitude"),
        "longitude": log_dict.get("longitude"),
        "odometer_km": log_dict.get("odometer_km"),
    }
    return compute_inputs_hash(relevant_fields)


def hash_gps_breadcrumb(crumb_dict: dict[str, Any]) -> str:
    """Hash integrity-relevant fields of a GPS breadcrumb (ADR-007)."""
    relevant_fields = {
        "tenant_id": crumb_dict.get("tenant_id"),
        "device_id": crumb_dict.get("device_id"),
        "driver_id": crumb_dict.get("driver_id"),
        "raw_id": crumb_dict.get("raw_id"),
        "event_timestamp": crumb_dict.get("event_timestamp"),
        "latitude": crumb_dict.get("latitude"),
        "longitude": crumb_dict.get("longitude"),
        "speed_kmh": crumb_dict.get("speed_kmh"),
        "odometer_m": crumb_dict.get("odometer_m"),
    }
    return compute_inputs_hash(relevant_fields)
