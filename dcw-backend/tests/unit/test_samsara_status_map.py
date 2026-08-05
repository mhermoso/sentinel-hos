"""Unit tests for Samsara hosStatusType → canonical mapping."""

from __future__ import annotations

import pytest

from app.domains.ingestion.adapters.samsara import _map_samsara_status
from app.domains.ingestion.schemas import CanonicalDutyStatus


@pytest.mark.parametrize(
    ("status_str", "expected"),
    [
        ("offDuty", CanonicalDutyStatus.OFF_DUTY),
        ("sleeperBed", CanonicalDutyStatus.SLEEPER_BERTH),
        ("driving", CanonicalDutyStatus.DRIVING),
        ("onDuty", CanonicalDutyStatus.ON_DUTY),
        ("yardMove", CanonicalDutyStatus.YARD_MOVE),
        ("personalConveyance", CanonicalDutyStatus.PERSONAL_CONVEYANCE),
        # Unknown / unrecognised → UNKNOWN
        ("sleeperBerth", CanonicalDutyStatus.UNKNOWN),  # Geotab spelling — not Samsara
        ("OFF", CanonicalDutyStatus.UNKNOWN),
        ("unknownStatus", CanonicalDutyStatus.UNKNOWN),
        ("", CanonicalDutyStatus.UNKNOWN),
        (None, CanonicalDutyStatus.UNKNOWN),
    ],
)
def test_map_samsara_status(
    status_str: str | None,
    expected: CanonicalDutyStatus,
) -> None:
    assert _map_samsara_status(status_str) is expected
