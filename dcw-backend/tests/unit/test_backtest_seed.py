"""Unit tests for backtest runner keys, payload shape, and Redis load path."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.domains.dashboard.day_builder import load_backtest_dispatches
from app.domains.engine.backtest_runner import (
    backtest_dispatches_key,
    bootstrap_backtest_key,
    serialize_dispatch_payload,
)


def test_backtest_dispatches_key_includes_tenant() -> None:
    assert backtest_dispatches_key("b_b_bros_transport") == (
        "backtest:dispatches:b_b_bros_transport"
    )


def test_bootstrap_backtest_key_includes_days_and_tenant() -> None:
    assert bootstrap_backtest_key("b_b_bros_transport", 30) == (
        "bootstrap:backtest-dispatches:30d:v1:b_b_bros_transport"
    )


def test_backtest_seed_settings_default_to_30_days() -> None:
    s = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
    )
    assert s.BACKTEST_SEED_ON_STARTUP is True
    assert s.BACKTEST_SEED_DAYS == 30


def test_serialize_dispatch_payload_shape() -> None:
    result = {
        "meta": {"mode": "event", "tenant_id": "t1"},
        "summary": {
            "raw_violation_count": 10,
            "would_dispatch_count": 3,
            "by_rule_severity_raw": {},
        },
        "dispatch_events": [{"driver_id": "d1", "as_of": "2026-01-01T00:00:00+00:00"}],
    }
    payload = serialize_dispatch_payload(result)
    assert payload["meta"]["tenant_id"] == "t1"
    assert payload["summary"]["would_dispatch_count"] == 3
    assert len(payload["dispatches"]) == 1
    assert "by_rule_severity_raw" not in payload["summary"]


@pytest.mark.asyncio
async def test_load_backtest_dispatches_reads_redis_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = "b_b_bros_transport"
    stored = {
        "dispatches": [
            {
                "driver_id": "drv1",
                "as_of": "2026-03-01T12:00:00+00:00",
                "violation_type": "DRIVING_LIMIT",
                "severity": "WARNING",
            }
        ]
    }

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=json.dumps(stored))

    async def fake_get_redis() -> AsyncMock:
        return fake_redis

    monkeypatch.setattr(
        "app.domains.dashboard.day_builder.settings.GEOTAB_DATABASE",
        tenant,
    )
    monkeypatch.setattr(
        "app.domains.dashboard.day_builder.get_redis",
        fake_get_redis,
    )

    rows = await load_backtest_dispatches(path=Path("/nonexistent/backtest.json"))
    assert len(rows) == 1
    assert rows[0]["driver_id"] == "drv1"
    fake_redis.get.assert_awaited_once_with(backtest_dispatches_key(tenant))


@pytest.mark.asyncio
async def test_load_backtest_dispatches_falls_back_to_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dispatch_path = tmp_path / "backtest_dispatches.json"
    dispatch_path.write_text(
        json.dumps(
            {
                "dispatches": [
                    {
                        "driver_id": "drv2",
                        "as_of": "2026-03-02T12:00:00+00:00",
                        "violation_type": "WEEKLY_CYCLE",
                        "severity": "VIOLATION",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)

    async def fake_get_redis() -> AsyncMock:
        return fake_redis

    monkeypatch.setattr(
        "app.domains.dashboard.day_builder.settings.GEOTAB_DATABASE",
        "tenant_x",
    )
    monkeypatch.setattr(
        "app.domains.dashboard.day_builder.get_redis",
        fake_get_redis,
    )

    rows = await load_backtest_dispatches(path=dispatch_path)
    assert len(rows) == 1
    assert rows[0]["driver_id"] == "drv2"
