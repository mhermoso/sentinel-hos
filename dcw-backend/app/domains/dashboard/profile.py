"""Driver contact profile helpers (roster-backed dashboard DTO)."""

from __future__ import annotations

from app.domains.dashboard.schemas import DriverContactProfile
from app.domains.ingestion.schemas import DriverRosterEntry


def build_contact_profile(
    driver_id: str,
    roster: DriverRosterEntry | None,
) -> DriverContactProfile:
    """Map a roster row (or absence) into the UI contact profile DTO."""
    if roster is None:
        return DriverContactProfile(driver_id=driver_id, roster_found=False)
    return DriverContactProfile(
        driver_id=driver_id,
        display_name=roster.display_name,
        first_name=roster.first_name,
        last_name=roster.last_name,
        phone_e164=roster.phone_e164,
        current_device_id=roster.current_device_id,
        unit_label=roster.unit_label,
        profile_complete=roster.profile_complete,
        has_unit_assignment=roster.has_unit_assignment,
        is_active=roster.is_active,
        roster_found=True,
    )
