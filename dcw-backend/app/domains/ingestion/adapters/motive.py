"""Motive telematics adapter — stub for future implementation.

Implements the ``BaseTelematicsAdapter`` interface with placeholder
methods that raise ``NotImplementedError`` until the Motive v1/hos_logs
integration is built.
"""

from __future__ import annotations

from app.domains.ingestion.adapters import BaseTelematicsAdapter
from app.domains.ingestion.schemas import DCWCanonicalHOSLog


class MotiveAdapter(BaseTelematicsAdapter):
    """Placeholder adapter for Motive (KeepTruckin) API integration."""

    provider_name = "motive"

    async def connect(self) -> None:
        raise NotImplementedError(
            "Motive adapter not yet implemented. "
            "Configure MOTIVE_API_KEY and implement v1/hos_logs polling."
        )

    async def fetch_feed(
        self,
        tenant_id: str,
        from_cursor: str,
    ) -> tuple[list[DCWCanonicalHOSLog], str]:
        raise NotImplementedError("Motive adapter not yet implemented.")
