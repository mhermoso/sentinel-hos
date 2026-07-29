"""Append-only JSON-lines logger for compliance alert dispatch events."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.config import settings
from app.domains.notifier.schemas import ComplianceAlert

logger = logging.getLogger("dcw.notifier.alert_logger")


def log_alert_event(
    alert: ComplianceAlert,
    *,
    suppressed: bool,
    dispatch_action: str,
    suppression_reason: Optional[str] = None,
    voice_call_sid: Optional[str] = None,
    sms_sid: Optional[str] = None,
) -> None:
    """Append one JSON line to the compliance alerts log file."""
    record: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant_id": alert.tenant_id,
        "driver_id": alert.driver_id,
        "violation_type": alert.violation_type,
        "severity": alert.severity.value,
        "rule_ref": alert.rule_ref,
        "description": alert.description,
        "detected_at": alert.detected_at.isoformat(),
        "suppressed": suppressed,
        "dispatch_action": dispatch_action,
    }
    if suppression_reason:
        record["suppression_reason"] = suppression_reason
    if voice_call_sid:
        record["voice_call_sid"] = voice_call_sid
    if sms_sid:
        record["sms_sid"] = sms_sid

    log_path = Path(settings.ALERT_LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        logger.error("Failed to write alert log to %s: %s", log_path, exc)
