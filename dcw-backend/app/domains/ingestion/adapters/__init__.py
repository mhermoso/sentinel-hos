"""Abstract base class for telematics provider adapters.

Every provider (Geotab, Motive, Samsara) implements this interface so
the ingestion poller can poll any provider through a uniform contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Tuple

from app.domains.ingestion.schemas import DCWCanonicalHOSLog


class BaseTelematicsAdapter(ABC):
    """Adapter interface for telematics provider integrations."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short lowercase identifier (e.g. 'geotab', 'motive', 'samsara')."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Authenticate / establish session with the provider API."""
        ...

    @abstractmethod
    async def fetch_feed(
        self,
        tenant_id: str,
        from_cursor: str,
    ) -> tuple[list[DCWCanonicalHOSLog], str]:
        """Fetch a batch of HOS records starting from ``from_cursor``.

        Returns:
            Tuple of (validated canonical logs, next cursor token).
        """
        ...
