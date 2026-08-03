"""Great-circle (air-mile) distance helpers for short-haul radius checks.

FMCSA § 395.1(e) uses air miles (nautical miles). Pack modules call these
pure functions; no I/O.
"""

from __future__ import annotations

import math
from typing import Tuple

# Mean Earth radius in nautical / air miles (1 nmi = 1852 m).
EARTH_RADIUS_AIR_MILES: float = 3440.065

# Federal short-haul radius (§ 395.1(e)).
SHORT_HAUL_RADIUS_AIR_MILES: float = 150.0

# Practical depot tolerance for "returned to work-reporting location".
RETURN_RADIUS_AIR_MILES: float = 2.0


def haversine_air_miles(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Return great-circle distance in air miles between two WGS84 points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return EARTH_RADIUS_AIR_MILES * c


def distance_from_origin_air_miles(
    origin: Tuple[float, float],
    latitude: float,
    longitude: float,
) -> float:
    """Air-mile distance from ``origin`` ``(lat, lon)`` to a fix."""
    return haversine_air_miles(origin[0], origin[1], latitude, longitude)
