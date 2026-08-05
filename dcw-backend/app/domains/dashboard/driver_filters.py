"""Server-side filters for the Drivers UI list.

Assignment / profile predicates are provider-agnostic: they use HOS id
conventions (``unassigned:device:*``, ``UNKNOWN_DRIVER``) and canonical
roster flags only — never Geotab/Samsara types.
"""

from __future__ import annotations

from app.domains.dashboard.alert_filters import normalize_filter_str
from app.domains.dashboard.schemas import DriverListItemResponse
from app.domains.ingestion.roster import is_real_person_driver_id, is_unassigned_driver_id


def matches_assignment(
    driver: DriverListItemResponse,
    assignment: str | None,
) -> bool:
    """Filter by assigned people vs unassigned HOS sentinels.

    - ``assigned``: real person id with active roster (or no roster row yet
      treated as not assigned for the Assigned default view)
    - ``unassigned``: ``unassigned:*`` / ``UNKNOWN_DRIVER``
    - ``None`` / ``all``: no assignment gate
    """
    assignment_n = normalize_filter_str(assignment)
    if assignment_n is None:
        return True
    key = assignment_n.lower()
    if key == "assigned":
        if not is_real_person_driver_id(driver.driver_id):
            return False
        # Require an active roster row so Assigned means "known people".
        return driver.roster_active is True
    if key == "unassigned":
        return is_unassigned_driver_id(driver.driver_id)
    return True


def matches_profile(
    driver: DriverListItemResponse,
    profile: str | None,
) -> bool:
    """Filter by roster contact completeness.

    - ``complete``: roster ``profile_complete`` is True
    - ``incomplete``: real person with roster missing or incomplete
    - ``None`` / ``all``: no profile gate
    """
    profile_n = normalize_filter_str(profile)
    if profile_n is None:
        return True
    key = profile_n.lower()
    if key == "complete":
        return driver.profile_complete is True
    if key == "incomplete":
        if not is_real_person_driver_id(driver.driver_id):
            return False
        return driver.profile_complete is not True
    return True


def matches_on_unit(
    driver: DriverListItemResponse,
    on_unit: bool | None,
) -> bool:
    """When True, keep only drivers with ``has_unit_assignment``."""
    if on_unit is not True:
        return True
    return driver.has_unit_assignment is True


def filter_drivers(
    drivers: list[DriverListItemResponse],
    *,
    q: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    assignment: str | None = None,
    profile: str | None = None,
    on_unit: bool | None = None,
) -> list[DriverListItemResponse]:
    """Filter driver rows by search, status, mode, assignment, and profile."""
    q_n = normalize_filter_str(q)
    status_n = normalize_filter_str(status)
    mode_n = normalize_filter_str(mode)

    result = drivers

    if q_n:
        needle = q_n.lower()
        result = [
            d
            for d in result
            if needle in d.driver_id.lower()
            or (d.driver_name and needle in d.driver_name.lower())
            or (d.unit_label and needle in d.unit_label.lower())
        ]

    if status_n:
        status_upper = status_n.upper()
        result = [
            d for d in result if (d.current_status or "UNKNOWN").upper() == status_upper
        ]

    if mode_n:
        mode_lower = mode_n.lower()
        if mode_lower == "live":
            result = [d for d in result if d.is_live]
        elif mode_lower == "historical":
            result = [d for d in result if not d.is_live]

    result = [d for d in result if matches_assignment(d, assignment)]
    result = [d for d in result if matches_profile(d, profile)]
    result = [d for d in result if matches_on_unit(d, on_unit)]

    return result
