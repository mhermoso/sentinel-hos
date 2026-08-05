"""Active fleet preference for the HOS dashboard UI.

Each telematics connection is a Fleet (Geotab, Samsara, …). Operators pick
which fleet to view via query param or cookie; all dashboard queries then
filter ``tenant_id = fleet.fleet_id``.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response

from app.core.config import settings
from app.domains.ingestion.fleets import Fleet, list_enabled_fleets

COOKIE_NAME = "dcw_fleet"
COOKIE_MAX_AGE = 365 * 24 * 3600


def _fallback_fleet() -> Fleet:
    """Synthetic fleet when the registry is empty (misconfigured env)."""
    if settings.GEOTAB_DATABASE:
        return Fleet(
            fleet_id=settings.GEOTAB_DATABASE,
            provider="geotab",
            display_name=settings.GEOTAB_DATABASE,
            enabled=True,
        )
    return Fleet(
        fleet_id="default",
        provider="geotab",
        display_name="Default",
        enabled=True,
    )


def default_fleet(fleets: list[Fleet]) -> Fleet:
    """Prefer an enabled Geotab fleet; otherwise the first enabled fleet."""
    for fleet in fleets:
        if fleet.provider == "geotab":
            return fleet
    if fleets:
        return fleets[0]
    return _fallback_fleet()


async def resolve_fleet(
    request: Request,
    *,
    fleet_param: str | None = None,
) -> Fleet:
    """Resolve active fleet: query param → cookie → default (prefer geotab).

    Unknown ids are rejected (ignored) so tenant scope cannot be redirected
    to an arbitrary ``tenant_id``.
    """
    fleets = await list_enabled_fleets()
    by_id = {f.fleet_id: f for f in fleets}

    if fleet_param and fleet_param in by_id:
        return by_id[fleet_param]

    cookie = request.cookies.get(COOKIE_NAME)
    if cookie and cookie in by_id:
        return by_id[cookie]

    return default_fleet(fleets)


def set_fleet_cookie(response: Response, fleet_id: str) -> None:
    """Persist the fleet preference cookie (caller must validate ``fleet_id``)."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=fleet_id,
        max_age=COOKIE_MAX_AGE,
        httponly=False,
        samesite="lax",
        path="/",
    )
