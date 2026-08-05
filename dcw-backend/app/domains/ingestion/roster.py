"""Provider-agnostic roster helpers (phone normalize, completeness flags).

Used by adapter mappers when building ``DriverRosterEntry``. Dashboard
filters consume the stored flags — never provider-specific types.
"""

from __future__ import annotations

import re
from typing import Any

UNASSIGNED_DRIVER_PREFIX = "unassigned:"
UNKNOWN_DRIVER_ID = "UNKNOWN_DRIVER"


def nonempty_str(value: Any) -> str | None:
    """Return stripped string or None when empty/missing."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_phone_e164(value: Any) -> str | None:
    """Best-effort E.164-ish normalize for US/CA-centric fleets.

    Keeps leading ``+`` when present; otherwise strips non-digits and
    prefixes ``+1`` for 10-digit numbers. Returns None when unusable.
    """
    text = nonempty_str(value)
    if text is None:
        return None
    if text.startswith("+"):
        digits = re.sub(r"\D", "", text)
        return f"+{digits}" if len(digits) >= 8 else None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) >= 8:
        return f"+{digits}"
    return None


def split_display_name(name: str | None) -> tuple[str | None, str | None]:
    """Soft heuristic: first token → first, remainder → last."""
    text = nonempty_str(name)
    if text is None:
        return None, None
    parts = text.split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def derive_profile_complete(
    *,
    first_name: str | None,
    last_name: str | None,
    display_name: str | None,
    phone_e164: str | None,
    require_first_last: bool = False,
) -> bool:
    """Usable contact: name + phone.

    When ``require_first_last`` (Geotab), both first and last are required.
    Otherwise a non-empty display name is enough (Samsara-style).
    """
    if not phone_e164:
        return False
    if require_first_last:
        return bool(first_name and last_name)
    if display_name:
        return True
    return bool(first_name and last_name)


def derive_has_unit_assignment(current_device_id: str | None) -> bool:
    return bool(nonempty_str(current_device_id))


def is_unassigned_driver_id(driver_id: str) -> bool:
    """True for ``unassigned:device:*`` and ``UNKNOWN_DRIVER`` HOS sentinels."""
    return driver_id.startswith(UNASSIGNED_DRIVER_PREFIX) or driver_id == UNKNOWN_DRIVER_ID


def is_real_person_driver_id(driver_id: str) -> bool:
    """True when the HOS/driver id is not an unassigned/unknown sentinel."""
    return not is_unassigned_driver_id(driver_id)
