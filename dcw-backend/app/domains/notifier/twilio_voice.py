"""Twilio Voice IVR dispatch — multi-language speech alert system."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.core.config import settings
from app.domains.notifier.schemas import ComplianceAlert
from app.domains.notifier.twilio_client import get_twilio_client, twilio_configured

logger = logging.getLogger("dcw.notifier.twilio_voice")


def _build_twiml_alert(alert: ComplianceAlert, language: str = "en") -> str:
    """Build TwiML XML for the compliance alert IVR flow."""
    messages = {
        "en": (
            f"This is an automated safety alert from Driver Compliance Watch. "
            f"Driver {alert.driver_name or alert.driver_id}, "
            f"you have a compliance warning: {alert.description}. "
            f"Press 1 to acknowledge or say your name to confirm you have heard this message."
        ),
        "es": (
            f"Este es un aviso de seguridad automático de Driver Compliance Watch. "
            f"Conductor {alert.driver_name or alert.driver_id}, "
            f"tiene una advertencia de cumplimiento: {alert.description}."
        ),
        "fr": (
            f"Ceci est une alerte de sécurité automatique de Driver Compliance Watch. "
            f"Chauffeur {alert.driver_name or alert.driver_id}, "
            f"vous avez un avertissement de conformité: {alert.description}."
        ),
    }

    msg = messages.get(language, messages["en"])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech dtmf" timeout="5" numDigits="1">
        <Say voice="Polly.Joanna" language="{language}">{msg}</Say>
    </Gather>
    <Say>We did not receive your input. Goodbye.</Say>
</Response>"""


async def place_voice_call(
    alert: ComplianceAlert,
    to_phone: str,
) -> Optional[str]:
    """Initiate a Twilio Voice IVR call for the compliance alert."""
    if settings.ALERT_DRY_RUN:
        logger.info(
            "[DRY_RUN] Voice call skipped: driver=%s phone=%s",
            alert.driver_id,
            to_phone,
        )
        return None

    if not twilio_configured():
        logger.warning(
            "Twilio credentials not configured — voice call skipped for driver %s",
            alert.driver_id,
        )
        return None

    client = get_twilio_client()
    if client is None:
        return None

    try:
        twiml = _build_twiml_alert(alert)
        loop = asyncio.get_running_loop()

        def _create_call() -> str:
            call = client.calls.create(
                twiml=twiml,
                to=to_phone,
                from_=settings.TWILIO_FROM_PHONE_NUMBER,
            )
            return str(call.sid)

        call_sid = await loop.run_in_executor(None, _create_call)
        logger.info("Voice call placed: sid=%s driver=%s", call_sid, alert.driver_id)
        return call_sid

    except Exception as exc:
        logger.error("Twilio voice call failed for driver %s: %s", alert.driver_id, exc)
        return None
