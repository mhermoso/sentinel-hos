"""Shared Twilio REST client factory.

Supports API-key authentication or Account SID + Auth Token (test/live).
"""

from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger("dcw.notifier.twilio_client")


def twilio_configured() -> bool:
    """Return True when enough credentials exist to place Twilio API calls."""
    if not settings.TWILIO_FROM_PHONE_NUMBER:
        return False

    api_key_ready = bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_API_KEY_SID
        and settings.TWILIO_API_KEY_SECRET
    )
    auth_token_ready = bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN)
    return api_key_ready or auth_token_ready


def get_twilio_client():
    """Build a Twilio REST client, or return None if credentials are incomplete."""
    if not twilio_configured():
        return None

    from twilio.rest import Client

    if (
        settings.TWILIO_API_KEY_SID
        and settings.TWILIO_API_KEY_SECRET
        and settings.TWILIO_ACCOUNT_SID
    ):
        return Client(
            settings.TWILIO_API_KEY_SID,
            settings.TWILIO_API_KEY_SECRET,
            settings.TWILIO_ACCOUNT_SID,
        )

    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
