"""Shared rules for HOS logs that must not interrupt prior duty status.

Geotab non-status events map to ``UNKNOWN``. Ignored logs and inactive
``eventRecordStatus`` values (2/3/4) behave the same: the prior OFF/SB/D/ON/PC/YM
status continues.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domains.ingestion.schemas import CanonicalDutyStatus

# Geotab DutyStatusLog.eventRecordStatus: 1=Active; 2/3/4 = inactive/pending/rejected.
INACTIVE_EVENT_RECORD_STATUSES = frozenset({2, 3, 4})


def should_skip_duty_status_change(
    status: str | CanonicalDutyStatus | None = None,
    raw_payload: Mapping[str, Any] | None = None,
) -> bool:
    """Return True when the log must not change the duty timeline."""
    status_val: str | None
    if status is None:
        status_val = None
    elif isinstance(status, CanonicalDutyStatus):
        status_val = status.value
    else:
        status_val = str(status)

    if status_val == CanonicalDutyStatus.UNKNOWN.value:
        return True

    if not raw_payload:
        return False

    if raw_payload.get("isIgnored") is True:
        return True

    ers = raw_payload.get("eventRecordStatus")
    if ers is None:
        return False
    try:
        return int(ers) in INACTIVE_EVENT_RECORD_STATUSES
    except (TypeError, ValueError):
        return False
