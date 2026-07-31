"""Unit tests for Geotab duty-status → canonical mapping (PC/YM exemptions)."""

from __future__ import annotations

import pytest

from app.domains.ingestion.adapters.geotab import _map_geotab_status
from app.domains.ingestion.schemas import CanonicalDutyStatus


@pytest.mark.parametrize(
    ("status_str", "origin", "expected"),
    [
        ("PC", "Manual", CanonicalDutyStatus.PERSONAL_CONVEYANCE),
        ("INT_PC", "Automatic", CanonicalDutyStatus.PERSONAL_CONVEYANCE),
        ("PersonalConveyance", None, CanonicalDutyStatus.PERSONAL_CONVEYANCE),
        ("EnginePowerupPC", None, CanonicalDutyStatus.PERSONAL_CONVEYANCE),
        ("EngineShutdownPC", None, CanonicalDutyStatus.PERSONAL_CONVEYANCE),
        ("YM", "Manual", CanonicalDutyStatus.YARD_MOVE),
        ("INT_YM", None, CanonicalDutyStatus.YARD_MOVE),
        ("YardMove", None, CanonicalDutyStatus.YARD_MOVE),
        # Origin fallback when status is a plain duty code
        ("ON", "YardMove", CanonicalDutyStatus.YARD_MOVE),
        ("OFF", "PersonalConveyance", CanonicalDutyStatus.PERSONAL_CONVEYANCE),
        # Standard statuses
        ("D", None, CanonicalDutyStatus.DRIVING),
        ("Driving", None, CanonicalDutyStatus.DRIVING),
        ("INT_D", None, CanonicalDutyStatus.DRIVING),
        ("OFF", None, CanonicalDutyStatus.OFF_DUTY),
        ("Off", None, CanonicalDutyStatus.OFF_DUTY),
        ("SB", None, CanonicalDutyStatus.SLEEPER_BERTH),
        ("SleeperBerth", None, CanonicalDutyStatus.SLEEPER_BERTH),
        ("ON", None, CanonicalDutyStatus.ON_DUTY),
        ("On", None, CanonicalDutyStatus.ON_DUTY),
        # Motion-only exemption events stay UNKNOWN (no invented PC/YM)
        ("DrivingWhileInExemption", None, CanonicalDutyStatus.UNKNOWN),
        ("DrivingStoppedWhileInExemption", None, CanonicalDutyStatus.UNKNOWN),
        ("EngineSyncCompliance", None, CanonicalDutyStatus.UNKNOWN),
    ],
)
def test_map_geotab_status(
    status_str: str | None,
    origin: str | None,
    expected: CanonicalDutyStatus,
) -> None:
    assert _map_geotab_status(status_str, origin) is expected
