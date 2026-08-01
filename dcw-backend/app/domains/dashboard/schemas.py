"""Pydantic response schemas for the DCW dashboard API layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DriverStatusResponse(BaseModel):
    """Live driver status pulled from Redis + latest audit record."""

    driver_id: str
    driver_name: Optional[str] = None
    tenant_id: str
    current_status: str
    last_event_at: Optional[datetime] = None
    is_compliant: bool = True
    driving_remaining_minutes: Optional[float] = None
    duty_window_remaining_minutes: Optional[float] = None
    break_required: bool = False
    weekly_hours_used: Optional[float] = None
    active_violation_count: int = 0


class HOSEventResponse(BaseModel):
    """A single HOS event in a driver timeline response."""

    raw_id: str
    status: str
    event_timestamp: datetime
    device_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    odometer_km: Optional[float] = None
    annotation: Optional[str] = None
    inputs_hash: str


class DriverTimelineResponse(BaseModel):
    """Full HOS event timeline for a driver."""

    driver_id: str
    tenant_id: str
    total_events: int
    events: List[HOSEventResponse]


class ViolationResponse(BaseModel):
    """A single violation in a compliance snapshot response."""

    violation_type: str
    severity: str
    rule_ref: str
    description: str
    detected_at: datetime
    overage_seconds: float = 0.0


class ComplianceSnapshotResponse(BaseModel):
    """Latest compliance evaluation result for a driver."""

    driver_id: str
    tenant_id: str
    evaluated_at: datetime
    rule_pack_version: str
    is_compliant: bool
    driving_remaining_seconds: float
    duty_window_remaining_seconds: float
    break_required: bool
    weekly_hours_used: float
    weekly_hours_remaining: float
    violations: List[ViolationResponse] = Field(default_factory=list)


class AuditRecordResponse(BaseModel):
    """Summary of a persisted audit record."""

    id: str
    tenant_id: str
    driver_id: str
    evaluated_at: datetime
    rule_pack_version: str
    is_compliant: bool
    weekly_hours_used: float
    driving_remaining_seconds: float
    violation_count: int = 0


class PaginatedAuditResponse(BaseModel):
    """Paginated list of audit records."""

    total: int
    limit: int
    offset: int
    records: List[AuditRecordResponse]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    environment: str
    database: str = "unknown"
    redis: str = "unknown"
    rule_pack_version: str
