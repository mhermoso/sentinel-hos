"""Shared alert filter normalization and default date windows."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.domains.dashboard.timezone import zoneinfo_for


def normalize_filter_str(value: str | None) -> str | None:
    """Treat empty / whitespace / literal 'all' as no filter (None)."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() == "all":
        return None
    return cleaned


def default_alerts_local_range(display_tz: str) -> tuple[date, date]:
    """Last 30 local calendar days inclusive (today and 29 days prior)."""
    today = datetime.now(zoneinfo_for(display_tz)).date()
    return today - timedelta(days=29), today


def local_dates_to_utc_window(
    from_date: date,
    to_date: date,
    display_tz: str,
) -> tuple[datetime, datetime]:
    """Inclusive local dates → UTC [start, end) window."""
    zone = zoneinfo_for(display_tz)
    start = datetime(from_date.year, from_date.month, from_date.day, tzinfo=zone).astimezone(
        ZoneInfo("UTC")
    )
    end = (
        datetime(to_date.year, to_date.month, to_date.day, tzinfo=zone) + timedelta(days=1)
    ).astimezone(ZoneInfo("UTC"))
    return start, end


def default_alerts_utc_window(display_tz: str) -> tuple[datetime, datetime]:
    from_d, to_d = default_alerts_local_range(display_tz)
    return local_dates_to_utc_window(from_d, to_d, display_tz)


def quick_range_dates(key: str, display_tz: str) -> tuple[date, date]:
    """Return (from_date, to_date) for a quick-range chip key."""
    today = datetime.now(zoneinfo_for(display_tz)).date()
    if key == "7d":
        return today - timedelta(days=6), today
    if key == "21d":
        return today - timedelta(days=20), today
    if key == "30d":
        return today - timedelta(days=29), today
    if key == "current_month":
        return today.replace(day=1), today
    if key == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        return first_prev, last_prev
    return default_alerts_local_range(display_tz)


def detect_active_range(from_date: date, to_date: date, display_tz: str) -> str | None:
    """Return chip key if from/to match a quick range, else None."""
    for key in ("7d", "21d", "30d", "current_month", "last_month"):
        start, end = quick_range_dates(key, display_tz)
        if start == from_date and end == to_date:
            return key
    return None


def month_end(year: int, month: int) -> date:
    """Last calendar day of the given month (utility for tests)."""
    return date(year, month, calendar.monthrange(year, month)[1])
