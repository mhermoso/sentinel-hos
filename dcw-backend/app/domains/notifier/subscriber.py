"""Redis pub/sub subscriber — listens for compliance alert events.

Runs as a long-lived async coroutine, processing messages from the
``compliance_alerts`` channel and dispatching voice calls / SMS through
the Twilio notifiers (with alert-lock suppression).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.redis import COMPLIANCE_ALERTS_CHANNEL, get_redis
from app.domains.notifier.alert_logger import log_alert_event
from app.domains.notifier.schemas import AlertStage, ComplianceAlert
from app.domains.notifier.suppressor import acquire_alert_lock, should_suppress_alert
from app.domains.notifier.twilio_sms import send_sms_alert
from app.domains.notifier.twilio_voice import place_voice_call

logger = logging.getLogger("dcw.notifier.subscriber")


def _resolve_phones(alert: ComplianceAlert) -> ComplianceAlert:
    """Apply test phone overrides from settings when configured."""
    driver_phone = settings.TWILIO_TEST_TO_PHONE or alert.driver_phone
    dispatcher_phone = settings.TWILIO_TEST_DISPATCHER_PHONE or alert.dispatcher_phone
    if driver_phone == alert.driver_phone and dispatcher_phone == alert.dispatcher_phone:
        return alert
    return alert.model_copy(
        update={
            "driver_phone": driver_phone or None,
            "dispatcher_phone": dispatcher_phone or None,
        }
    )


def _parse_alert_event(message_data: str) -> ComplianceAlert | None:
    """Parse a raw Redis pub/sub message into a ComplianceAlert."""
    try:
        payload = json.loads(message_data)
        violation = payload.get("violation", {})
        return ComplianceAlert(
            tenant_id=payload["tenant_id"],
            driver_id=payload["driver_id"],
            violation_type=violation.get("violation_type", "UNKNOWN"),
            severity=AlertStage(violation.get("severity", "WARNING")),
            rule_ref=violation.get("rule_ref", ""),
            description=violation.get("description", ""),
            detected_at=datetime.fromisoformat(
                violation.get("detected_at", datetime.now(UTC).isoformat())
            ),
            overage_seconds=violation.get("overage_seconds", 0.0),
        )
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse alert event: %s — %s", message_data[:200], exc)
        return None


async def _dispatch_alert(alert: ComplianceAlert) -> None:
    """Apply suppression check then dispatch voice + SMS."""
    alert = _resolve_phones(alert)
    shift_id = datetime.now(UTC).strftime("%Y%m%d")

    suppressed, reason = await should_suppress_alert(
        tenant_id=alert.tenant_id,
        driver_id=alert.driver_id,
        shift_id=shift_id,
        rule=alert.violation_type,
        stage=alert.severity.value,
    )

    if suppressed:
        log_alert_event(
            alert,
            suppressed=True,
            dispatch_action="suppressed",
            suppression_reason=reason,
        )
        logger.debug("Alert suppressed: %s", reason)
        return

    acquired = await acquire_alert_lock(
        tenant_id=alert.tenant_id,
        driver_id=alert.driver_id,
        shift_id=shift_id,
        rule=alert.violation_type,
        stage=alert.severity.value,
    )

    if not acquired:
        log_alert_event(
            alert,
            suppressed=True,
            dispatch_action="lock_race",
            suppression_reason="Alert lock race",
        )
        logger.debug("Alert lock race — skipping duplicate dispatch")
        return

    logger.info(
        "Dispatching alert: driver=%s rule=%s severity=%s dry_run=%s",
        alert.driver_id,
        alert.rule_ref,
        alert.severity,
        settings.ALERT_DRY_RUN,
    )

    voice_sid: str | None = None
    sms_sid: str | None = None
    dispatch_action = "dry_run" if settings.ALERT_DRY_RUN else "dispatch"

    if alert.driver_phone and alert.severity in (
        AlertStage.VIOLATION,
        AlertStage.CRITICAL,
    ):
        if settings.ALERT_DRY_RUN:
            dispatch_action = "skipped_dry_run_voice"
        else:
            voice_sid = await place_voice_call(alert=alert, to_phone=alert.driver_phone)
            dispatch_action = "voice" if voice_sid else "voice_failed"

    if alert.dispatcher_phone:
        if settings.ALERT_DRY_RUN:
            dispatch_action = "skipped_dry_run_sms" if dispatch_action == "dry_run" else dispatch_action
        else:
            sms_sid = await send_sms_alert(alert=alert, to_phone=alert.dispatcher_phone)
            dispatch_action = "sms" if sms_sid else "sms_failed"

    log_alert_event(
        alert,
        suppressed=False,
        dispatch_action=dispatch_action,
        voice_call_sid=voice_sid,
        sms_sid=sms_sid,
    )


async def run_subscriber_loop() -> None:
    """Async loop that subscribes to Redis and processes compliance alerts."""
    logger.info("Starting compliance alert subscriber on channel: %s", COMPLIANCE_ALERTS_CHANNEL)

    while True:
        try:
            redis_client = await get_redis()
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(COMPLIANCE_ALERTS_CHANNEL)
            logger.info("Subscribed to %s", COMPLIANCE_ALERTS_CHANNEL)

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = message.get("data", "")
                if not data:
                    continue
                alert = _parse_alert_event(data)
                if alert:
                    await _dispatch_alert(alert)

        except (aioredis.RedisError, ConnectionError) as exc:
            logger.error("Redis subscriber error: %s — retrying in 5s", exc)
            await asyncio.sleep(5)
        except Exception as exc:
            logger.error("Unexpected subscriber error: %s — retrying in 10s", exc, exc_info=True)
            await asyncio.sleep(10)
