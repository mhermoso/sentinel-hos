"""MyGeotab User helpers shared by the adapter and history backfill."""

from __future__ import annotations

import mygeotab


def build_geotab_driver_name_map(api: mygeotab.API) -> dict[str, str]:
    """Fetch MyGeotab ``User`` records and map id → full name."""
    users = api.get("User")
    name_map: dict[str, str] = {}
    for user in users:
        uid = user.get("id")
        if not uid:
            continue
        first = user.get("firstName", "") or ""
        last = user.get("lastName", "") or ""
        full_name = f"{first} {last}".strip() or user.get("name", uid)
        name_map[str(uid)] = str(full_name)
    return name_map
