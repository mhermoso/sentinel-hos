"""Motive telematics adapter — stub for future implementation.

Implements the ``BaseTelematicsAdapter`` interface with placeholder
methods that raise ``NotImplementedError`` until the Motive v1/hos_logs
integration is built. Roster fetch stubs mirror ``fetch_feed`` so Motive
plugs into the same sync path when HOS goes live.
"""

from __future__ import annotations

from app.domains.ingestion.adapters import BaseTelematicsAdapter
from app.domains.ingestion.schemas import (
    DCWCanonicalHOSLog,
    DriverRosterEntry,
    VehicleRosterEntry,
)


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

    async def fetch_driver_roster(self, tenant_id: str) -> list[DriverRosterEntry]:
        raise NotImplementedError(
            "Motive roster not yet implemented. "
            "Map GET /v1/users?role=driver into DriverRosterEntry when HOS ships."
        )

    async def fetch_vehicle_roster(self, tenant_id: str) -> list[VehicleRosterEntry]:
        raise NotImplementedError(
            "Motive vehicle roster not yet implemented. "
            "Map GET /v1/vehicles (current_driver) into VehicleRosterEntry when HOS ships."
        )
