"""Redis pub/sub subscriber — listens for compliance alert events.

Runs as a long-lived async coroutine, processing messages from the
``compliance_alerts`` channel and dispatching voice calls / SMS through
the Twilio notifiers (with alert-lock suppression).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.redis import COMPLIANCE_ALERTS_CHANNEL, get_redis
from app.domains.notifier.schemas import AlertStage, ComplianceAlert
from app.domains.notifier.suppressor import acquire_alert_lock, should_suppress_alert
from app.domains.notifier.twilio_sms import send_sms_alert
from app.domains.notifier.twilio_voice import place_voice_call

logger = logging.getLogger("dcw.notifier.subscriber")


def _parse_alert_event(message_data: str) -> Optional[ComplianceAlert]:
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
                violation.get("detected_at", datetime.now(timezone.utc).isoformat())
            ),
            overage_seconds=violation.get("overage_seconds", 0.0),
        )
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to parse alert event: %s — %s", message_data[:200], exc)
        return None


async def _dispatch_alert(alert: ComplianceAlert) -> None:
    """Apply suppression check then dispatch voice + SMS."""
    shift_id = datetime.now(timezone.utc).strftime("%Y%m%d")

    suppressed, reason = await should_suppress_alert(
        tenant_id=alert.tenant_id,
        driver_id=alert.driver_id,
        shift_id=shift_id,
        rule=alert.violation_type,
        stage=alert.severity.value,
    )

    if suppressed:
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
        logger.debug("Alert lock race — skipping duplicate dispatch")
        return

    logger.info(
        "Dispatching alert: driver=%s rule=%s severity=%s",
        alert.driver_id,
        alert.rule_ref,
        alert.severity,
    )

    if alert.driver_phone and alert.severity in (
        AlertStage.VIOLATION,
        AlertStage.CRITICAL,
    ):
        await place_voice_call(alert=alert, to_phone=alert.driver_phone)

    if alert.dispatcher_phone:
        await send_sms_alert(alert=alert, to_phone=alert.dispatcher_phone)


async def run_subscriber_loop() -> None:
    """Async loop that subscribes to Redis and processes compliance alerts.

    This coroutine runs indefinitely and should be started during
    app startup via asyncio.create_task().
    """
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
