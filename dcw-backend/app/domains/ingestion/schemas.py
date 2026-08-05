"""Canonical HOS data models for the DCW ingestion layer.

These Pydantic v2 schemas are the system-wide contract (ADR-003).
All telematics provider payloads are mapped to ``DCWCanonicalHOSLog``
upon ingestion.  Downstream engine and notifier layers depend only on
these canonical types.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

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
    driver_name: str | None = Field(None, description="Driver's full name")
    raw_id: str = Field(..., description="Provider-specific record ID")
    status: CanonicalDutyStatus
    event_timestamp: datetime = Field(
        ..., description="UTC timestamp of HOS status change"
    )
    device_id: str | None = Field(None, description="Assigned vehicle device ID")
    latitude: float | None = Field(None, ge=-90.0, le=90.0)
    longitude: float | None = Field(None, ge=-180.0, le=180.0)
    odometer_km: float | None = Field(
        None,
        ge=0.0,
        description="Vehicle odometer in meters (Geotab). Field name is legacy.",
    )
    annotation: str | None = Field(None, max_length=500)
    raw_payload: dict[str, Any] = Field(
        ..., description="Sanitised snapshot of original provider JSON payload"
    )
    inputs_hash: str | None = Field(
        None, description="SHA-256 digest of compliance-relevant fields"
    )

    @field_validator("event_timestamp", mode="before")
    @classmethod
    def parse_datetime(cls, value: Any) -> datetime:
        """Standardize ISO string or provider datetime to UTC-aware datetime."""
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            else:
                dt = dt.astimezone(UTC)
            return dt
        elif isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        return value


class DCWGpsBreadcrumb(BaseModel):
    """Canonical GPS breadcrumb for route maps (ADR-007).

    Separate from ``DCWCanonicalHOSLog`` — not an HOS duty-status event.
    The compliance engine must never consume this type.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    tenant_id: str = Field(..., description="Unique customer database identifier")
    device_id: str = Field(..., description="Vehicle / telematics device ID")
    driver_id: str = Field(
        ...,
        description="Resolved driver ID (or unassigned:device:{id} fallback)",
    )
    raw_id: str = Field(..., description="Provider-specific record ID")
    event_timestamp: datetime = Field(..., description="UTC timestamp of GPS fix")
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    speed_kmh: float | None = Field(
        None,
        ge=0.0,
        description="Vehicle speed in km/h when reported by provider",
    )
    raw_payload: dict[str, Any] = Field(
        ..., description="Sanitised snapshot of original provider JSON payload"
    )
    inputs_hash: str | None = Field(
        None, description="SHA-256 digest of integrity-relevant fields"
    )

    @field_validator("event_timestamp", mode="before")
    @classmethod
    def parse_datetime(cls, value: Any) -> datetime:
        """Standardize ISO string or provider datetime to UTC-aware datetime."""
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            else:
                dt = dt.astimezone(UTC)
            return dt
        elif isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        return value


class IngestionBatchResult(BaseModel):
    """Result summary returned by an ingestion polling cycle."""

    tenant_id: str
    provider: str
    records_fetched: int = 0
    records_valid: int = 0
    records_rejected: int = 0
    next_cursor: str = ""
    driver_ids_seen: list[str] = Field(default_factory=list)


class DriverRosterEntry(BaseModel):
    """Provider-agnostic driver roster row for sync + dashboard filters.

    Built by telematics adapters; never carries Geotab/Samsara/Motive types
    into the dashboard layer.
    """

    model_config = ConfigDict(frozen=True)

    provider: str = Field(..., description="geotab | samsara | motive")
    tenant_id: str = Field(..., description="Fleet partition key (fleet_id)")
    external_driver_id: str = Field(..., description="Provider driver/user id")
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    phone_e164: str | None = None
    current_device_id: str | None = None
    unit_label: str | None = None
    is_active: bool = True
    profile_complete: bool = False
    has_unit_assignment: bool = False


class VehicleRosterEntry(BaseModel):
    """Minimal vehicle roster row (unit label / VIN / optional current driver)."""

    model_config = ConfigDict(frozen=True)

    provider: str
    tenant_id: str
    external_device_id: str
    name: str | None = None
    vin: str | None = None
    current_driver_id: str | None = None
