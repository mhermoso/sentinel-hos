"""Twilio SMS fallback engine."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import settings
from app.domains.notifier.schemas import ComplianceAlert
from app.domains.notifier.twilio_client import get_twilio_client, twilio_configured

logger = logging.getLogger("dcw.notifier.twilio_sms")


def _build_sms_body(alert: ComplianceAlert) -> str:
    """Build a concise SMS message for the compliance alert."""
    severity_emoji = {
        "WARNING": "⚠️",
        "VIOLATION": "🚨",
        "CRITICAL": "🔴",
    }.get(alert.severity.value, "⚠️")

    return (
        f"{severity_emoji} DCW HOS ALERT {severity_emoji}\n"
        f"Driver: {alert.driver_name or alert.driver_id}\n"
        f"Rule: {alert.rule_ref}\n"
        f"{alert.description}\n"
        f"Time: {alert.detected_at.strftime('%Y-%m-%d %H:%M UTC')}"
    )


async def send_sms_alert(
    alert: ComplianceAlert,
    to_phone: str,
) -> str | None:
    """Send a Twilio SMS compliance alert to a dispatcher or driver."""
    if settings.ALERT_DRY_RUN:
        logger.info(
            "[DRY_RUN] SMS skipped: driver=%s phone=%s body=%s",
            alert.driver_id,
            to_phone,
            _build_sms_body(alert).replace("\n", " | "),
        )
        return None

    if not twilio_configured():
        logger.warning(
            "Twilio credentials not configured — SMS skipped for driver %s",
            alert.driver_id,
        )
        return None

    client = get_twilio_client()
    if client is None:
        return None

    try:
        body = _build_sms_body(alert)
        loop = asyncio.get_running_loop()

        def _create_message() -> str:
            message = client.messages.create(
                body=body,
                to=to_phone,
                from_=settings.TWILIO_FROM_PHONE_NUMBER,
            )
            return str(message.sid)

        sms_sid = await loop.run_in_executor(None, _create_message)
        logger.info("SMS sent: sid=%s driver=%s", sms_sid, alert.driver_id)
        return sms_sid

    except Exception as exc:
        logger.error("Twilio SMS failed for driver %s: %s", alert.driver_id, exc)
        return None
