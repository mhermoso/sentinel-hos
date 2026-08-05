"""Pydantic response schemas for the DCW dashboard API layer."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class DriverStatusResponse(BaseModel):
    """Live driver status pulled from Redis + latest audit record."""

    driver_id: str
    driver_name: str | None = None
    tenant_id: str
    current_status: str
    last_event_at: datetime | None = None
    is_compliant: bool = True
    driving_remaining_minutes: float | None = None
    duty_window_remaining_minutes: float | None = None
    break_required: bool = False
    weekly_hours_used: float | None = None
    active_violation_count: int = 0


class DriverListItemResponse(BaseModel):
    """Driver row for the full historical + live picker."""

    driver_id: str
    driver_name: str | None = None
    tenant_id: str
    is_live: bool = False
    event_count: int = 0
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    current_status: str | None = None
    # Roster-derived flags (None when no driver_roster row yet)
    roster_active: bool | None = None
    profile_complete: bool | None = None
    has_unit_assignment: bool | None = None
    unit_label: str | None = None


class DriverListResponse(BaseModel):
    """All drivers with HOS history and/or live activity."""

    tenant_id: str
    timezone: str
    total: int
    drivers: list[DriverListItemResponse]


class DaySegmentAlertResponse(BaseModel):
    """Alert that fires during a day activity-log segment."""

    as_of: datetime
    violation_type: str
    severity: str
    rule_ref: str = ""
    description: str = ""
    source: str = ""


class DayStatusEventResponse(BaseModel):
    """A status change clipped to a home-terminal day (grid-eligible statuses only).

    ``lane`` is the Y-axis row (OFF/SB/D/ON). PC uses lane OFF; YM uses lane ON.
    """

    status: str
    lane: str = ""
    event_timestamp: datetime
    local_timestamp: str
    local_end_timestamp: str = ""
    hour_of_day: float
    duration_seconds: float
    duration_hhmm: str
    duration_hms: str = ""
    distance_m: float = 0.0
    distance_mi: float = 0.0
    distance_km: float = 0.0
    distance_label: str = ""
    origin: str = ""
    annotation: str | None = None
    device_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_label: str = ""
    continued: bool = False
    alerts: list[DaySegmentAlertResponse] = Field(default_factory=list)


class DurationTotalsResponse(BaseModel):
    """OFF / SB / D / ON / UNKNOWN duration totals for one local day.

    PC seconds are included in OFF; YM seconds in ON. Separate exemption
    fields expose the striped PC/YM portions for the legend. UNKNOWN is
    unmapped provider time and counts toward the tracked 24h total.
    Distances are odometer deltas (Geotab meters → mi/km).
    """

    OFF: str
    SB: str
    D: str
    ON: str
    UNKNOWN: str = "00:00"
    OFF_seconds: float = 0.0
    SB_seconds: float = 0.0
    D_seconds: float = 0.0
    ON_seconds: float = 0.0
    UNKNOWN_seconds: float = 0.0
    exemption_pc_seconds: float = 0.0
    exemption_ym_seconds: float = 0.0
    exemption_pc: str = "00:00"
    exemption_ym: str = "00:00"
    total_hhmm: str
    covers_full_day: bool = False
    distance_m: float = 0.0
    distance_mi: float = 0.0
    distance_km: float = 0.0
    distance_label: str = ""
    D_mi: float = 0.0
    ON_mi: float = 0.0
    OFF_mi: float = 0.0
    SB_mi: float = 0.0
    exemption_pc_mi: float = 0.0
    exemption_ym_mi: float = 0.0


class AlertMarkerResponse(BaseModel):
    """Alert marker for overlay on the HOS day grid."""

    as_of: datetime
    local_timestamp: str
    hour_of_day: float
    violation_type: str
    severity: str
    rule_ref: str = ""
    description: str = ""
    source: str = Field(..., description="backtest | live_audit")
    driver_id: str = ""
    driver_name: str | None = None


class DriverDayResponse(BaseModel):
    """HOS status grid payload for one driver / local calendar day."""

    driver_id: str
    driver_name: str | None = None
    tenant_id: str
    date: date
    timezone: str
    day_start_utc: datetime
    day_end_utc: datetime
    is_live: bool = False
    carry_forward_status: str | None = None
    events: list[DayStatusEventResponse]
    totals: DurationTotalsResponse
    alert_markers: list[AlertMarkerResponse] = Field(default_factory=list)


class RouteSegmentResponse(BaseModel):
    """Status-colored polyline segment between two GPS breadcrumbs."""

    status: str
    color: str
    lat1: float
    lon1: float
    lat2: float
    lon2: float
    t0: datetime
    t1: datetime


class RouteAlertPointResponse(BaseModel):
    """Alert marker placed on the day route map."""

    as_of: datetime
    severity: str
    violation_type: str
    rule_ref: str = ""
    description: str = ""
    source: str = ""
    lat: float
    lon: float


class DriverDayRouteMeta(BaseModel):
    """Metadata for a driver-day route response."""

    driver_id: str
    date: date
    point_count: int = 0
    segment_count: int = 0
    downsampled_count: int = 0
    coverage_note: str = ""


class DriverDayRouteResponse(BaseModel):
    """GPS route trail + alert points for one driver / local calendar day."""

    segments: list[RouteSegmentResponse] = Field(default_factory=list)
    alerts: list[RouteAlertPointResponse] = Field(default_factory=list)
    meta: DriverDayRouteMeta


class AlertMarkersResponse(BaseModel):
    """Merged backtest + live audit markers for a time window."""

    driver_id: str
    from_ts: datetime
    to_ts: datetime
    markers: list[AlertMarkerResponse]


class HOSEventResponse(BaseModel):
    """A single HOS event in a driver timeline response."""

    raw_id: str
    status: str
    event_timestamp: datetime
    device_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    odometer_km: float | None = None
    annotation: str | None = None
    inputs_hash: str


class DriverTimelineResponse(BaseModel):
    """Full HOS event timeline for a driver."""

    driver_id: str
    tenant_id: str
    total_events: int
    events: list[HOSEventResponse]


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
    violations: list[ViolationResponse] = Field(default_factory=list)


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
    records: list[AuditRecordResponse]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    environment: str
    database: str = "unknown"
    redis: str = "unknown"
    rule_pack_version: str


class AlertExplanationStep(BaseModel):
    """One plain-language step in the calculation explanation."""

    step: str
    value: str
    note: str = ""


class AlertClocksResponse(BaseModel):
    """Clock snapshot at alert recompute time."""

    driving_used_h: float
    driving_remaining_h: float
    driving_limit_h: float
    duty_used_h: float
    duty_remaining_h: float
    duty_limit_h: float
    weekly_used_h: float
    weekly_remaining_h: float
    weekly_limit_h: float
    break_required: bool = False
    driving_since_break_h: float = 0.0
    consecutive_rest_h: float = 0.0
    last_valid_restart_at: str | None = None
    last_valid_restart_at_local: str | None = None
    had_34h_restart: bool = False
    weekly_window_mode: str = "rolling_window"
    weekly_window_subtitle: str = "rolling window"


class AlertContextEventResponse(BaseModel):
    """Zoomed HOS segment for the alert context graph."""

    status: str
    lane: str = ""
    event_timestamp: str
    local_timestamp: str
    hour_offset: float
    duration_seconds: float
    duration_hhmm: str
    fraction_start: float
    fraction_end: float
    highlighted: bool = False


class AlertDetailMeta(BaseModel):
    """Identity fields for an alert detail drawer."""

    driver_id: str
    driver_name: str | None = None
    as_of: datetime
    local_time: str
    display_timezone: str
    violation_type: str
    severity: str
    rule_ref: str = ""
    description: str = ""
    source: str = ""
    rule_pack_version: str = ""
    matched_on_recompute: bool = True


class AlertShiftWindowResponse(BaseModel):
    """Shift / weekly window used for the clocks that fired the alert."""

    label: str = ""
    start_utc: str | None = None
    start_local: str = ""
    end_utc: str | None = None
    end_local: str = ""
    note: str = ""


class AlertWeeklyRestartResponse(BaseModel):
    """34h restart applied-or-not context for the weekly clock."""

    had_restart: bool = False
    restart_at_utc: str | None = None
    restart_at_local: str | None = None
    window_mode: str = "rolling_window"
    window_mode_label: str = "rolling window"
    weekly_window_start_local: str = ""
    message: str = ""


class AlertDetailResponse(BaseModel):
    """Full calculation detail payload for an alert marker click."""

    meta: AlertDetailMeta
    clocks: AlertClocksResponse
    explanation: list[AlertExplanationStep] = Field(default_factory=list)
    overage_seconds: float = 0.0
    context_events: list[AlertContextEventResponse] = Field(default_factory=list)
    context_window: dict[str, Any] = Field(default_factory=dict)
    shift_window: AlertShiftWindowResponse | None = None
    weekly_restart: AlertWeeklyRestartResponse | None = None
    day_date: str = ""


class FleetAlertItemResponse(BaseModel):
    """One row in the fleet Alerts tab / API."""

    as_of: datetime
    local_timestamp: str
    hour_of_day: float = 0.0
    driver_id: str
    driver_name: str | None = None
    violation_type: str
    severity: str
    rule_ref: str = ""
    description: str = ""
    source: str
    day_date: str = ""


class FleetAlertsResponse(BaseModel):
    """Filtered fleet alert list."""

    total: int
    timezone: str
    alerts: list[FleetAlertItemResponse]


class DriverPositionResponse(BaseModel):
    """Latest known lat/lon for a driver (from canonical HOS logs)."""

    driver_id: str
    driver_name: str | None = None
    status: str | None = None
    latitude: float
    longitude: float
    event_timestamp: datetime
    is_live: bool = False
    warning_count: int = 0
    violation_count: int = 0
    latest_alert_severity: str | None = None
    latest_alert_type: str | None = None


class DriverPositionsResponse(BaseModel):
    """Fleet driver positions for the Home map."""

    tenant_id: str
    total: int
    positions: list[DriverPositionResponse]


class RecentIngestionItemResponse(BaseModel):
    """One recently ingested canonical HOS log (Geotab arrival feed)."""

    ingested_at: datetime
    event_timestamp: datetime
    driver_id: str
    driver_name: str | None = None
    status: str
    device_id: str | None = None
    raw_id: str
    latitude: float | None = None
    longitude: float | None = None


class RecentIngestionResponse(BaseModel):
    """Newest canonical HOS logs by ingested_at for the live Geotab feed."""

    tenant_id: str
    total: int
    events: list[RecentIngestionItemResponse]


class DispatchLogItemResponse(BaseModel):
    """One row from the compliance alerts JSONL dispatch log."""

    timestamp: str | None = None
    driver_id: str = ""
    driver_name: str | None = None
    severity: str = ""
    violation_type: str = ""
    channel: str = ""
    dispatch_action: str = ""
    suppressed: bool = False
    description: str = ""
    voice_call_sid: str | None = None
    sms_sid: str | None = None


class DispatchLogResponse(BaseModel):
    """Latest Twilio / dry-run dispatch history from the JSONL log."""

    total: int
    path: str
    events: list[DispatchLogItemResponse]


class OpsLogItemResponse(BaseModel):
    """One row from the dcw.* ops JSONL event log."""

    timestamp: str | None = None
    level: str = "INFO"
    logger: str = ""
    message: str = ""
    process: str = ""


class OpsLogResponse(BaseModel):
    """Latest operational log events from logs/ops-events.log."""

    total: int
    path: str
    events: list[OpsLogItemResponse]
