"""GetFeed cursor must not advance past unmapped DutyStatusLog records.

Regression: ValidationError was logged as a fake \"DLQ\" while ``toVersion``
still advanced, permanently skipping HOS events Geotab will not redeliver.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.domains.ingestion.adapters.geotab import GeotabAdapter
from app.domains.ingestion.schemas import CanonicalDutyStatus


def _duty_status_log(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "a1",
        "driver": {"id": "drv-1"},
        "device": {"id": "dev-1"},
        "status": "Driving",
        "origin": "Automatic",
        "dateTime": "2026-08-05T12:00:00.000Z",
        "location": {"x": -87.62, "y": 41.87},
        "odometer": 1000,
        "comment": None,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_fetch_feed_holds_cursor_when_record_fails_validation() -> None:
    adapter = GeotabAdapter()
    adapter.api = MagicMock()
    adapter.api.call.return_value = {
        "result": [
            _duty_status_log(id="good-1"),
            # Missing dateTime → DCWCanonicalHOSLog ValidationError
            _duty_status_log(id="bad-1", dateTime=None),
            _duty_status_log(id="good-2", status="Off", dateTime="2026-08-05T13:00:00.000Z"),
        ],
        "toVersion": "00000000000000ff",
    }

    logs, next_cursor = await adapter.fetch_feed(
        tenant_id="bbbBros",
        from_cursor="0000000000000000",
    )

    assert next_cursor == "0000000000000000"
    assert [log.raw_id for log in logs] == ["good-1", "good-2"]
    assert logs[0].status is CanonicalDutyStatus.DRIVING
    assert logs[1].status is CanonicalDutyStatus.OFF_DUTY


@pytest.mark.asyncio
async def test_fetch_feed_advances_cursor_when_all_records_valid() -> None:
    adapter = GeotabAdapter()
    adapter.api = MagicMock()
    adapter.api.call.return_value = {
        "result": [
            _duty_status_log(id="good-1"),
            _duty_status_log(id="good-2", status="On", dateTime="2026-08-05T14:00:00.000Z"),
        ],
        "toVersion": "00000000000000aa",
    }

    logs, next_cursor = await adapter.fetch_feed(
        tenant_id="bbbBros",
        from_cursor="0000000000000000",
    )

    assert next_cursor == "00000000000000aa"
    assert len(logs) == 2


@pytest.mark.asyncio
async def test_fetch_feed_holds_cursor_when_entire_batch_invalid() -> None:
    adapter = GeotabAdapter()
    adapter.api = MagicMock()
    # Out-of-range latitude via Geotab location.y → permanent ValidationError
    # under prior code would still advance toVersion and lose the record.
    adapter.api.call.return_value = {
        "result": [_duty_status_log(id="bad-lat", location={"x": -87.0, "y": 999.0})],
        "toVersion": "00000000000000bb",
    }

    logs, next_cursor = await adapter.fetch_feed(
        tenant_id="bbbBros",
        from_cursor="0000000000000001",
    )

    assert logs == []
    assert next_cursor == "0000000000000001"
