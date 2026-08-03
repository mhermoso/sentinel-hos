"""Regression: one driver failure must not abort the rest of the fleet sweep."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.engine.schemas import (
    ComplianceResult,
    DriverTimeline,
    Violation,
    ViolationSeverity,
    ViolationType,
)
from app.domains.engine.sweeper import sweep_active_drivers


def _timeline(driver_id: str) -> DriverTimeline:
    return DriverTimeline(
        driver_id=driver_id,
        tenant_id="tenant",
        events=[
            DriverTimeline.HOSEvent(
                status="OFF",
                timestamp=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            ),
            DriverTimeline.HOSEvent(
                status="D",
                timestamp=datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc),
            ),
        ],
    )


def _result(driver_id: str, *, with_violation: bool = False) -> ComplianceResult:
    violations: List[Violation] = []
    if with_violation:
        violations.append(
            Violation(
                violation_type=ViolationType.DRIVING_LIMIT,
                severity=ViolationSeverity.WARNING,
                rule_ref="§ 395.3(a)(3)(i)",
                description="approaching limit",
                detected_at=datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc),
            )
        )
    return ComplianceResult(
        tenant_id="tenant",
        driver_id=driver_id,
        evaluated_at=datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc),
        rule_pack_version="fmcsa-us-property@1.3.0",
        inputs_hash="a" * 64,
        driving_remaining_seconds=1800.0,
        duty_window_remaining_seconds=3600.0,
        break_required=False,
        weekly_hours_used=40.0,
        weekly_hours_remaining=30.0,
        violations=violations,
    )


class _SessionCtx:
    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_sweeper_rolls_back_and_continues_after_driver_db_error() -> None:
    session = AsyncMock()
    session.commit = AsyncMock(side_effect=[RuntimeError("flush failed"), None])
    session.rollback = AsyncMock()

    repo = MagicMock()
    repo.get_driver_timeline = AsyncMock(
        side_effect=[_timeline("driver-bad"), _timeline("driver-good")]
    )
    repo.persist_audit_record = AsyncMock()

    rule_pack = MagicMock()
    rule_pack.evaluate = MagicMock(
        side_effect=[_result("driver-bad", with_violation=True), _result("driver-good")]
    )

    with (
        patch(
            "app.domains.engine.sweeper.IngestionRepository.get_active_driver_ids",
            new=AsyncMock(return_value=["driver-bad", "driver-good"]),
        ),
        patch(
            "app.domains.engine.sweeper.async_session_factory",
            return_value=_SessionCtx(session),
        ),
        patch("app.domains.engine.sweeper.EngineRepository", return_value=repo),
        patch("app.domains.engine.sweeper._rule_pack", rule_pack),
        patch(
            "app.domains.engine.sweeper.compute_weekly_duty_seconds",
            return_value=10.0 * 3600,
        ),
        patch(
            "app.domains.engine.sweeper.compute_inputs_hash",
            return_value="b" * 64,
        ),
        patch(
            "app.domains.engine.sweeper.publish_event",
            new=AsyncMock(),
        ),
        patch("app.domains.engine.sweeper.settings") as settings,
    ):
        settings.GEOTAB_DATABASE = "tenant"
        settings.WEEKLY_CYCLE_DAYS = 8
        settings.DEFAULT_RULE_PACK_VERSION = "fmcsa-us-property@1.3.0"

        out = await sweep_active_drivers({})

    assert out["drivers_swept"] == 1
    assert session.rollback.await_count == 1
    assert repo.persist_audit_record.await_count == 2
    assert session.commit.await_count == 2
