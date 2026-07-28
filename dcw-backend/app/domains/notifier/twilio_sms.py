"""Twilio SMS fallback engine.

Sends immediate text messages to dispatchers and safety officers when
voice calls are unacknowledged or for lower-severity warnings.

Stub implementation — wire in real Twilio SDK when credentials are set.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings
from app.domains.notifier.schemas import ComplianceAlert

logger = logging.getLogger("dcw.notifier.twilio_sms")


def _build_sms_body(alert: ComplianceAlert) -> str:
    """Build a concise SMS message for the compliance alert."""
    severity_emoji = {
        "WARNING": "⚠️",
        "VIOLATION": "🚨",
        "CRITICAL": "🔴",
    }.get(alert.severity, "⚠️")

    remaining_info = ""
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
) -> Optional[str]:
    """Send a Twilio SMS compliance alert to a dispatcher or driver.

    Args:
        alert: The compliance alert to communicate.
        to_phone: Target phone number in E.164 format.

    Returns:
        Twilio message SID if sent successfully, None on failure.

    Note:
        Stub — configure TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN to enable.
    """
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning(
            "Twilio credentials not configured — SMS skipped for driver %s",
            alert.driver_id,
        )
        return None

    try:
        # Real implementation:
        # from twilio.rest import Client
        # client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        # message = client.messages.create(
        #     body=_build_sms_body(alert),
        #     to=to_phone,
        #     from_=settings.TWILIO_FROM_PHONE_NUMBER,
        # )
        # return message.sid

        body = _build_sms_body(alert)
        logger.info(
            "[STUB] SMS would be sent to %s: %s",
            to_phone,
            body.replace("\n", " | "),
        )
        return "STUB_SMS_SID"

    except Exception as exc:
        logger.error("Twilio SMS failed for driver %s: %s", alert.driver_id, exc)
        return None
