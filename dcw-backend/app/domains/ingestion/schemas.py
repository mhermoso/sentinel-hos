"""Canonical HOS data models for the DCW ingestion layer.

These Pydantic v2 schemas are the system-wide contract (ADR-003).
All telematics provider payloads are mapped to ``DCWCanonicalHOSLog``
upon ingestion.  Downstream engine and notifier layers depend only on
these canonical types.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CanonicalDutyStatus(str, Enum):
    """Canonical Hours of Service (HOS) duty statuses for DCW engine."""

    OFF_DUTY = "OFF"
    SLEEPER_BERTH = "SB"
    DRIVING = "D"
    ON_DUTY = "ON"
    YARD_MOVE = "YM"
    PERSONAL_CONVEYANCE = "PC"
    UNKNOWN = "UNKNOWN"


class DCWCanonicalHOSLog(BaseModel):
    """Canonical data model representing an HOS Log event in DCW.

    This is the universal immutable record written to ``canonical_hos_logs``.
    All provider adapters produce instances of this model.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    tenant_id: str = Field(..., description="Unique customer database identifier")
    driver_id: str = Field(..., description="Normalized driver ID")
    driver_name: Optional[str] = Field(None, description="Driver's full name")
    raw_id: str = Field(..., description="Provider-specific record ID")
    status: CanonicalDutyStatus
    event_timestamp: datetime = Field(
        ..., description="UTC timestamp of HOS status change"
    )
    device_id: Optional[str] = Field(None, description="Assigned vehicle device ID")
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0)
    odometer_km: Optional[float] = Field(
        None,
        ge=0.0,
        description="Vehicle odometer in meters (Geotab). Field name is legacy.",
    )
    annotation: Optional[str] = Field(None, max_length=500)
    raw_payload: Dict[str, Any] = Field(
        ..., description="Sanitised snapshot of original provider JSON payload"
    )
    inputs_hash: Optional[str] = Field(
        None, description="SHA-256 digest of compliance-relevant fields"
    )

    @field_validator("event_timestamp", mode="before")
    @classmethod
    def parse_datetime(cls, value: Any) -> datetime:
        """Standardize ISO string or provider datetime to UTC-aware datetime."""
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        elif isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value


class IngestionBatchResult(BaseModel):
    """Result summary returned by an ingestion polling cycle."""

    tenant_id: str
    provider: str
    records_fetched: int = 0
    records_valid: int = 0
    records_rejected: int = 0
    next_cursor: str = ""
    driver_ids_seen: List[str] = Field(default_factory=list)
