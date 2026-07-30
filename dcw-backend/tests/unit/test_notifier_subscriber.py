"""Unit tests for compliance alert subscriber dispatch guards."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

import pytest

from app.domains.notifier.schemas import AlertStage, ComplianceAlert
from app.domains.notifier import subscriber as subscriber_module


def _alert(**overrides: Any) -> ComplianceAlert:
    base: Dict[str, Any] = {
        "tenant_id": "t1",
        "driver_id": "d1",
        "violation_type": "DRIVING_LIMIT",
        "severity": AlertStage.VIOLATION,
        "rule_ref": "§ 395.3(a)(3)(i)",
        "description": "over limit",
        "detected_at": datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
        "overage_seconds": 60.0,
    }
    base.update(overrides)
    return ComplianceAlert(**base)


@pytest.mark.asyncio
async def test_missing_phones_skips_without_acquiring_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def _should_suppress(**_kwargs: Any) -> tuple[bool, None]:
        calls.append("suppress_check")
        return False, None

    async def _acquire(**_kwargs: Any) -> bool:
        calls.append("acquire")
        return True

    logged: list[Dict[str, Any]] = []

    def _log(alert: ComplianceAlert, **kwargs: Any) -> None:
        logged.append({"alert": alert, **kwargs})

    monkeypatch.setattr(subscriber_module, "should_suppress_alert", _should_suppress)
    monkeypatch.setattr(subscriber_module, "acquire_alert_lock", _acquire)
    monkeypatch.setattr(subscriber_module, "log_alert_event", _log)
    monkeypatch.setattr(subscriber_module.settings, "TWILIO_TEST_TO_PHONE", "")
    monkeypatch.setattr(subscriber_module.settings, "TWILIO_TEST_DISPATCHER_PHONE", "")

    await subscriber_module._dispatch_alert(_alert())

    assert calls == []
    assert len(logged) == 1
    assert logged[0]["dispatch_action"] == "missing_phone"
    assert logged[0]["suppressed"] is True


def test_parse_alert_event_reads_phones_from_payload() -> None:
    raw = (
        '{"tenant_id":"t1","driver_id":"d1","driver_phone":"+15551112222",'
        '"dispatcher_phone":"+15553334444","violation":{'
        '"violation_type":"DUTY_WINDOW","severity":"WARNING",'
        '"rule_ref":"§ 395.3(a)(2)","description":"approaching",'
        '"detected_at":"2026-03-01T12:00:00+00:00","overage_seconds":0}}'
    )
    alert = subscriber_module._parse_alert_event(raw)
    assert alert is not None
    assert alert.driver_phone == "+15551112222"
    assert alert.dispatcher_phone == "+15553334444"
