"""LogRecord poller must not advance past unmapped GPS breadcrumbs."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.ingestion.poller import DEFAULT_CURSOR, poll_geotab_log_records


@pytest.mark.asyncio
async def test_log_record_poll_holds_cursor_on_parse_failure() -> None:
    adapter = MagicMock()
    adapter.fetch_log_record_feed = AsyncMock(
        return_value=(
            [
                {
                    "id": "good",
                    "device": {"id": "dev-1"},
                    "dateTime": "2026-08-05T12:00:00Z",
                    "latitude": 41.0,
                    "longitude": -87.0,
                },
                {
                    "id": "bad-no-device",
                    "dateTime": "2026-08-05T12:01:00Z",
                    "latitude": 41.1,
                    "longitude": -87.1,
                },
            ],
            "00000000000000ff",
        )
    )
    ctx: dict[str, Any] = {"geotab_adapter": adapter}

    session = MagicMock()
    session.commit = AsyncMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "app.domains.ingestion.poller.IngestionRepository.load_cursor",
            new=AsyncMock(return_value=DEFAULT_CURSOR),
        ),
        patch(
            "app.domains.ingestion.poller.IngestionRepository.save_cursor",
            new=AsyncMock(),
        ) as save_cursor,
        patch(
            "app.domains.ingestion.poller.IngestionRepository.resolve_driver_for_device",
            new=AsyncMock(return_value="drv-1"),
        ),
        patch(
            "app.domains.ingestion.poller.IngestionRepository.persist_gps_breadcrumbs",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "app.domains.ingestion.poller.async_session_factory",
            return_value=session_cm,
        ),
        patch(
            "app.domains.ingestion.poller.settings.GEOTAB_DATABASE",
            "bbbBros",
        ),
    ):
        result = await poll_geotab_log_records(ctx)

    assert result["records_rejected"] == 1
    assert result["cursor"] == DEFAULT_CURSOR
    save_cursor.assert_awaited_once_with("geotab-logrecord", "bbbBros", DEFAULT_CURSOR)
