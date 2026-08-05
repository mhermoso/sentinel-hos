"""Append-only JSON-lines logger for compliance alert dispatch events."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.domains.notifier.schemas import ComplianceAlert

logger = logging.getLogger("dcw.notifier.alert_logger")


def log_alert_event(
    alert: ComplianceAlert,
    *,
    suppressed: bool,
    dispatch_action: str,
    suppression_reason: str | None = None,
    voice_call_sid: str | None = None,
    sms_sid: str | None = None,
) -> None:
    """Append one JSON line to the compliance alerts log file."""
    record: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
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


def read_alert_log(limit: int = 50, *, path: Path | None = None) -> list[dict[str, Any]]:
    """Read the newest ``limit`` JSONL records from the compliance alerts log.

    Does not place Twilio calls — file read only. Returns newest-first.
    """
    if limit < 1:
        return []
    log_path = path if path is not None else Path(settings.ALERT_LOG_PATH)
    if not log_path.is_file():
        return []

    try:
        # Efficient tail for typical log sizes: read all lines, take last N.
        with log_path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.error("Failed to read alert log from %s: %s", log_path, exc)
        return []

    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
        if len(records) >= limit:
            break
    return records
