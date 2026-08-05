"""Unit tests for SamsaraAdapter.fetch_feed (mocked SDK)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from samsara.errors import TooManyRequestsError

from app.domains.ingestion.adapters import samsara as samsara_mod
from app.domains.ingestion.adapters.samsara import SamsaraAdapter, map_samsara_log_to_canonical


class _Dumpable:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return self._data


def _page(
    *,
    driver: dict[str, Any],
    logs: list[dict[str, Any]],
    has_next: bool = False,
    end_cursor: str = "",
) -> MagicMock:
    group = MagicMock()
    group.driver = _Dumpable(driver)
    group.hos_logs = [_Dumpable(entry) for entry in logs]
    response = MagicMock()
    response.data = [group]
    response.pagination = MagicMock()
    response.pagination.has_next_page = has_next
    response.pagination.end_cursor = end_cursor
    return response


def _adapter_with_client(get_hos_logs: AsyncMock) -> SamsaraAdapter:
    adapter = SamsaraAdapter()
    adapter.fleet_id = "samsara:9005155"
    client = MagicMock()
    client.hours_of_service.get_hos_logs = get_hos_logs
    adapter.client = client
    return adapter


@pytest.mark.asyncio
async def test_fetch_feed_advances_watermark_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return frozen_now if tz is None else frozen_now.astimezone(tz)

    monkeypatch.setattr(samsara_mod, "datetime", _FrozenDateTime)
    monkeypatch.setattr(samsara_mod.settings, "SAMSARA_RESCAN_HOURS", 24)

    entry = {
        "hosStatusType": "driving",
        "logStartTime": "2026-08-05T11:00:00.000Z",
        "vehicle": {"id": "1"},
        "logRecordedLocation": {"latitude": 31.76, "longitude": -106.48},
    }
    get_hos = AsyncMock(
        return_value=_page(
            driver={"id": "d1", "name": "Driver One"},
            logs=[entry],
        )
    )
    adapter = _adapter_with_client(get_hos)

    logs, cursor = await adapter.fetch_feed("samsara:9005155", from_cursor="")
    assert len(logs) == 1
    assert cursor == "2026-08-05T12:00:00Z"
    assert logs[0].raw_id == "samsara:d1:2026-08-05T11:00:00.000Z:driving"


@pytest.mark.asyncio
async def test_fetch_feed_rescan_window_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_now = datetime(2026, 8, 5, 18, 0, 0, tzinfo=UTC)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return frozen_now if tz is None else frozen_now.astimezone(tz)

    monkeypatch.setattr(samsara_mod, "datetime", _FrozenDateTime)
    monkeypatch.setattr(samsara_mod.settings, "SAMSARA_RESCAN_HOURS", 6)

    get_hos = AsyncMock(
        return_value=_page(driver={"id": "d1"}, logs=[])
    )
    adapter = _adapter_with_client(get_hos)

    # Watermark older than rescan floor → start clamped to now - 6h
    old_watermark = "2026-08-01T00:00:00Z"
    _, cursor = await adapter.fetch_feed("samsara:9005155", from_cursor=old_watermark)
    assert cursor == "2026-08-05T18:00:00Z"

    call_kwargs = get_hos.await_args.kwargs
    expected_start = (frozen_now - timedelta(hours=6)).replace(microsecond=0)
    assert call_kwargs["start_time"] == expected_start.isoformat().replace("+00:00", "Z")
    assert call_kwargs["end_time"] == "2026-08-05T18:00:00Z"


@pytest.mark.asyncio
async def test_fetch_feed_frozen_params_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return frozen_now if tz is None else frozen_now.astimezone(tz)

    monkeypatch.setattr(samsara_mod, "datetime", _FrozenDateTime)
    monkeypatch.setattr(samsara_mod.settings, "SAMSARA_RESCAN_HOURS", 24)

    page1 = _page(
        driver={"id": "d1"},
        logs=[
            {
                "hosStatusType": "offDuty",
                "logStartTime": "2026-08-05T10:00:00.000Z",
            }
        ],
        has_next=True,
        end_cursor="cursor-page-2",
    )
    page2 = _page(
        driver={"id": "d1"},
        logs=[
            {
                "hosStatusType": "driving",
                "logStartTime": "2026-08-05T11:00:00.000Z",
            }
        ],
        has_next=False,
        end_cursor="",
    )
    get_hos = AsyncMock(side_effect=[page1, page2])
    adapter = _adapter_with_client(get_hos)

    logs, cursor = await adapter.fetch_feed("samsara:9005155", from_cursor="")
    assert len(logs) == 2
    assert cursor == "2026-08-05T12:00:00Z"
    assert get_hos.await_count == 2

    first = get_hos.await_args_list[0].kwargs
    second = get_hos.await_args_list[1].kwargs
    assert first["start_time"] == second["start_time"]
    assert first["end_time"] == second["end_time"]
    assert first["after"] is None
    assert second["after"] == "cursor-page-2"


@pytest.mark.asyncio
async def test_fetch_feed_rate_limit_mid_pagination_keeps_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return frozen_now if tz is None else frozen_now.astimezone(tz)

    monkeypatch.setattr(samsara_mod, "datetime", _FrozenDateTime)
    monkeypatch.setattr(samsara_mod.settings, "SAMSARA_RESCAN_HOURS", 24)

    page1 = _page(
        driver={"id": "d1"},
        logs=[
            {
                "hosStatusType": "driving",
                "logStartTime": "2026-08-05T11:00:00.000Z",
            }
        ],
        has_next=True,
        end_cursor="next",
    )
    get_hos = AsyncMock(side_effect=[page1, TooManyRequestsError(body={})])
    adapter = _adapter_with_client(get_hos)

    from_cursor = "2026-08-05T06:00:00Z"
    logs, cursor = await adapter.fetch_feed(
        "samsara:9005155",
        from_cursor=from_cursor,
    )
    assert len(logs) == 1
    assert cursor == from_cursor  # unchanged on rate-limit


def test_overlapping_polls_produce_dedupable_raw_ids() -> None:
    driver = {"id": "5250", "name": "Test"}
    entry = {
        "hosStatusType": "sleeperBed",
        "logStartTime": "2026-08-04T22:00:00.000Z",
        "vehicle": {"id": "0"},
    }
    a = map_samsara_log_to_canonical("samsara:9005155", driver, entry)
    b = map_samsara_log_to_canonical("samsara:9005155", driver, entry)
    assert a.raw_id == b.raw_id
    assert a.raw_id == "samsara:5250:2026-08-04T22:00:00.000Z:sleeperBed"
