"""Geotab feed partial template rendering."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.domains.dashboard.timezone import format_display_datetime

_TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "domains" / "dashboard" / "templates"


class _FeedEvent:
    """Minimal stand-in for RecentIngestionItemResponse in templates."""

    def __init__(self) -> None:
        self.ingested_at = datetime(2026, 7, 31, 2, 30, tzinfo=UTC)
        self.event_timestamp = datetime(2026, 7, 31, 2, 28, 19, tzinfo=UTC)
        self.driver_id = "b382"
        self.driver_name = "Cesar Garza"
        self.status = "PC"
        self.device_id = "b1"
        self.raw_id = "raw-1"
        self.latitude = 27.2
        self.longitude = -98.1


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["local_dt"] = format_display_datetime
    return env


def test_geotab_feed_renders_home_ssr_context() -> None:
    html = _env().get_template("partials/geotab_feed.html").render(
        feed_events=[_FeedEvent()],
        feed_newest_raw_id="raw-1",
        timezone="America/Chicago",
    )
    assert "geotab-feed" in html
    assert "Cesar Garza" in html
    assert "Loading feed" not in html


def test_geotab_feed_renders_htmx_partial_context() -> None:
    html = _env().get_template("partials/geotab_feed.html").render(
        events=[_FeedEvent()],
        newest_raw_id="raw-1",
        timezone="America/Chicago",
    )
    assert "feed-row is-newest" in html
    assert 'data-raw-id="raw-1"' in html


def test_geotab_feed_empty_state() -> None:
    html = _env().get_template("partials/geotab_feed.html").render(
        feed_events=[],
        feed_newest_raw_id="",
        timezone="America/Chicago",
    )
    assert "No Geotab HOS logs ingested yet." in html
