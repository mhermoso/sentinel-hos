"""Logs page partial template rendering."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.domains.dashboard.ops_feed import LogFeedRow
from app.domains.dashboard.timezone import format_display_datetime

_TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "domains" / "dashboard" / "templates"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["local_dt"] = format_display_datetime
    return env


def test_logs_feed_renders_rows_and_services() -> None:
    row = LogFeedRow(
        timestamp=datetime(2026, 7, 31, 2, 30, tzinfo=timezone.utc),
        source="ingestion",
        level="INFO",
        driver_id="b382",
        driver_name="Cesar Garza",
        message="Ingested D",
        process="worker",
    )
    html = _env().get_template("partials/logs_feed.html").render(
        rows=[row],
        row_count=1,
        source="all",
        filter_chips=[
            {"key": "all", "label": "All"},
            {"key": "ingestion", "label": "Ingestion"},
        ],
        services={
            "api": {"status": "healthy", "label": "API", "detail": "Serving"},
            "worker": {"status": "healthy", "label": "Worker", "detail": "Active (3s ago)"},
        },
        timezone="America/Chicago",
        tz_abbrev="CDT",
    )
    assert "logs-services" in html
    assert "Cesar Garza" in html
    assert "Ingested D" in html
    assert 'source-tag source-ingestion' in html
    assert "Worker" in html


def test_logs_feed_empty_state() -> None:
    html = _env().get_template("partials/logs_feed.html").render(
        rows=[],
        row_count=0,
        source="engine",
        filter_chips=[{"key": "engine", "label": "Engine"}],
        services={
            "api": {"status": "healthy", "label": "API", "detail": "Serving"},
        },
        timezone="America/Chicago",
        tz_abbrev="CDT",
    )
    assert "No log events for this filter yet" in html
