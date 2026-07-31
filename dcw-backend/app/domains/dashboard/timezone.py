"""Display timezone preference for the HOS dashboard UI.

Default is Central Time (``America/Chicago``). Users can override via
query param or cookie; engine home-terminal TZ for 34h restart rules
remains ``settings.DEFAULT_HOME_TERMINAL_TIMEZONE``.
"""

from __future__ import annotations

from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from fastapi.responses import Response

from app.core.config import settings

COOKIE_NAME = "dcw_display_tz"
COOKIE_MAX_AGE = 365 * 24 * 3600

# Short curated list for the nav selector (MVP).
DISPLAY_TIMEZONES: tuple[tuple[str, str], ...] = (
    ("America/Chicago", "Central (Chicago)"),
    ("America/New_York", "Eastern (New York)"),
    ("America/Denver", "Mountain (Denver)"),
    ("America/Los_Angeles", "Pacific (Los Angeles)"),
    ("America/Phoenix", "Arizona (Phoenix)"),
    ("UTC", "UTC"),
)

_ALLOWED = {tz for tz, _ in DISPLAY_TIMEZONES}


def default_display_timezone() -> str:
    """Fleet default — Central unless overridden in settings."""
    candidate = settings.DEFAULT_HOME_TERMINAL_TIMEZONE or "America/Chicago"
    if candidate in _ALLOWED:
        return candidate
    # If settings uses an IANA name outside the select list, still allow it.
    try:
        ZoneInfo(candidate)
        return candidate
    except ZoneInfoNotFoundError:
        return "America/Chicago"


def is_valid_timezone(name: str) -> bool:
    if not name:
        return False
    if name in _ALLOWED:
        return True
    try:
        ZoneInfo(name)
        return True
    except ZoneInfoNotFoundError:
        return False


def zoneinfo_for(name: Optional[str] = None) -> ZoneInfo:
    tz_name = name or default_display_timezone()
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Chicago")


def resolve_display_timezone(
    request: Request,
    *,
    tz_param: Optional[str] = None,
) -> str:
    """Resolve display TZ: query param → cookie → default Central."""
    if tz_param and is_valid_timezone(tz_param):
        return tz_param
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie and is_valid_timezone(cookie):
        return cookie
    return default_display_timezone()


def set_display_timezone_cookie(response: Response, tz_name: str) -> None:
    if not is_valid_timezone(tz_name):
        tz_name = default_display_timezone()
    response.set_cookie(
        key=COOKIE_NAME,
        value=tz_name,
        max_age=COOKIE_MAX_AGE,
        httponly=False,
        samesite="lax",
        path="/",
    )


def tz_abbreviation(tz_name: str, at: Optional[object] = None) -> str:
    """Return CST/CDT-style abbreviation for labels."""
    from datetime import datetime, timezone

    zone = zoneinfo_for(tz_name)
    when = at if isinstance(at, datetime) else datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(zone).tzname() or tz_name
