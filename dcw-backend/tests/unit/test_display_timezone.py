"""Display timezone formatting for dashboard UI."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.dashboard.timezone import (
    format_display_clock,
    format_display_date,
    format_display_datetime,
)


def test_format_display_datetime_chicago() -> None:
    utc = datetime(2026, 7, 31, 2, 30, 0, tzinfo=UTC)
    assert format_display_datetime(utc, "America/Chicago") == "2026-07-30 21:30:00"
    assert format_display_date(utc, "America/Chicago") == "2026-07-30"
    assert format_display_clock(utc, "America/Chicago") == "21:30:00"


def test_format_display_datetime_utc() -> None:
    utc = datetime(2026, 7, 31, 2, 30, 0, tzinfo=UTC)
    assert format_display_datetime(utc, "UTC") == "2026-07-31 02:30:00"


def test_format_display_datetime_none() -> None:
    assert format_display_datetime(None, "America/Chicago") == "—"
