"""Samsara telematics adapter — stub for future implementation.

Implements the ``BaseTelematicsAdapter`` interface with placeholder
methods that raise ``NotImplementedError`` until the Samsara
fleet/hos/logs integration is built.
"""

from __future__ import annotations

from typing import List, Tuple

from app.domains.ingestion.adapters import BaseTelematicsAdapter
from app.domains.ingestion.schemas import DCWCanonicalHOSLog


class SamsaraAdapter(BaseTelematicsAdapter):
    """Placeholder adapter for Samsara API integration."""

    provider_name = "samsara"

    async def connect(self) -> None:
        raise NotImplementedError(
            "Samsara adapter not yet implemented. "
            "Configure SAMSARA_API_TOKEN and implement fleet/hos/logs polling."
        )

    async def fetch_feed(
        self,
        tenant_id: str,
        from_cursor: str,
    ) -> Tuple[List[DCWCanonicalHOSLog], str]:
        raise NotImplementedError("Samsara adapter not yet implemented.")
