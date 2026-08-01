"""Server-side filters for the Drivers UI list."""

from __future__ import annotations

from typing import Optional

from app.domains.dashboard.alert_filters import normalize_filter_str
from app.domains.dashboard.schemas import DriverListItemResponse


def filter_drivers(
    drivers: list[DriverListItemResponse],
    *,
    q: Optional[str] = None,
    status: Optional[str] = None,
    mode: Optional[str] = None,
) -> list[DriverListItemResponse]:
    """Filter driver rows by search text, duty status, and live/historical mode."""
    q_n = normalize_filter_str(q)
    status_n = normalize_filter_str(status)
    mode_n = normalize_filter_str(mode)

    result = drivers

    if q_n:
        needle = q_n.lower()
        result = [
            d
            for d in result
            if needle in d.driver_id.lower()
            or (d.driver_name and needle in d.driver_name.lower())
        ]

    if status_n:
        status_upper = status_n.upper()
        result = [
            d for d in result if (d.current_status or "UNKNOWN").upper() == status_upper
        ]

    if mode_n:
        mode_lower = mode_n.lower()
        if mode_lower == "live":
            result = [d for d in result if d.is_live]
        elif mode_lower == "historical":
            result = [d for d in result if not d.is_live]

    return result
