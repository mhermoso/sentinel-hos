"""Pydantic schemas for the DCW notifier (alerting & telephony) domain."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class AlertStage(str, Enum):
    """Alert urgency stage used for alert-lock key construction."""

    WARNING = "WARNING"
    VIOLATION = "VIOLATION"
    CRITICAL = "CRITICAL"


class ComplianceAlert(BaseModel):
    """Alert event deserialized from the Redis ``compliance_alerts`` channel."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    driver_id: str
    violation_type: str
    severity: AlertStage
    rule_ref: str
    description: str
    detected_at: datetime
    overage_seconds: float = 0.0

    # Contact info — populated from tenant account at dispatch time
    driver_phone: Optional[str] = Field(None, description="Driver mobile number")
    dispatcher_phone: Optional[str] = Field(None, description="Safety officer number")
    driver_name: Optional[str] = None


class AlertDispatchResult(BaseModel):
    """Outcome of a single alert dispatch attempt."""

    alert: ComplianceAlert
    voice_call_sid: Optional[str] = None
    sms_sid: Optional[str] = None
    suppressed: bool = False
    suppression_reason: Optional[str] = None
    dispatched_at: datetime = Field(default_factory=lambda: datetime.utcnow())
