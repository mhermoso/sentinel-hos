"""Build the unified activity feed for the dashboard Logs page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.core.ops_log import read_ops_log
from app.domains.dashboard.driver_names import resolve_driver_name
from app.domains.notifier.alert_logger import read_alert_log

LogSource = Literal["system", "ingestion", "alerts", "engine"]
LogFilter = Literal["all", "system", "ingestion", "alerts", "engine"]

WORKER_STALE_SECONDS = 300


@dataclass(frozen=True)
class LogFeedRow:
    """One row in the Logs activity table."""

    timestamp: datetime
    source: LogSource
    level: str
    driver_id: str
    driver_name: str | None
    message: str
    process: str = ""


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _classify_ops_logger(logger_name: str) -> LogSource:
    name = logger_name.lower()
    if "ingestion" in name or "adapters.geotab" in name:
        return "ingestion"
    if "engine" in name or "sweeper" in name or "rule_pack" in name:
        return "engine"
    if "notifier" in name:
        return "alerts"
    return "system"


def rows_from_ops(limit: int = 100) -> list[LogFeedRow]:
    rows: list[LogFeedRow] = []
    for rec in read_ops_log(limit=limit):
        ts = _parse_ts(rec.get("timestamp"))
        if ts is None:
            continue
        logger_name = str(rec.get("logger") or "")
        rows.append(
            LogFeedRow(
                timestamp=ts,
                source=_classify_ops_logger(logger_name),
                level=str(rec.get("level") or "INFO"),
                driver_id="",
                driver_name=None,
                message=str(rec.get("message") or ""),
                process=str(rec.get("process") or ""),
            )
        )
    return rows


def rows_from_alerts(limit: int = 50) -> list[LogFeedRow]:
    rows: list[LogFeedRow] = []
    for rec in read_alert_log(limit=limit):
        ts = _parse_ts(rec.get("timestamp"))
        if ts is None:
            continue
        driver_id = str(rec.get("driver_id") or "")
        action = str(rec.get("dispatch_action") or "")
        severity = str(rec.get("severity") or "")
        vtype = str(rec.get("violation_type") or "")
        desc = str(rec.get("description") or "")
        parts = [p for p in (action, severity, vtype, desc) if p]
        rows.append(
            LogFeedRow(
                timestamp=ts,
                source="alerts",
                level=severity or "INFO",
                driver_id=driver_id,
                driver_name=resolve_driver_name(driver_id) if driver_id else None,
                message=" — ".join(parts) if parts else "alert dispatch",
                process="notifier",
            )
        )
    return rows


def rows_from_ingestion(events: list[Any]) -> list[LogFeedRow]:
    rows: list[LogFeedRow] = []
    for ev in events:
        ts = _parse_ts(getattr(ev, "ingested_at", None))
        if ts is None:
            continue
        driver_id = str(getattr(ev, "driver_id", "") or "")
        status = str(getattr(ev, "status", "") or "")
        raw_id = str(getattr(ev, "raw_id", "") or "")
        event_ts = getattr(ev, "event_timestamp", None)
        event_label = ""
        if isinstance(event_ts, datetime):
            event_label = event_ts.astimezone(UTC).strftime("%H:%M:%S UTC")
        msg = f"Ingested {status}" + (f" @ {event_label}" if event_label else "")
        if raw_id:
            msg += f" (raw_id={raw_id})"
        rows.append(
            LogFeedRow(
                timestamp=ts,
                source="ingestion",
                level="INFO",
                driver_id=driver_id,
                driver_name=getattr(ev, "driver_name", None),
                message=msg,
                process="worker",
            )
        )
    return rows


def rows_from_audit(records: list[Any]) -> list[LogFeedRow]:
    rows: list[LogFeedRow] = []
    for rec in records:
        ts = _parse_ts(getattr(rec, "evaluated_at", None))
        if ts is None:
            continue
        driver_id = str(getattr(rec, "driver_id", "") or "")
        compliant = bool(getattr(rec, "is_compliant", True))
        vcount = int(getattr(rec, "violation_count", 0) or 0)
        pack = str(getattr(rec, "rule_pack_version", "") or "")
        status = "compliant" if compliant else f"{vcount} violation(s)"
        msg = f"Audit {status}"
        if pack:
            msg += f" [{pack}]"
        rows.append(
            LogFeedRow(
                timestamp=ts,
                source="engine",
                level="INFO" if compliant else "WARNING",
                driver_id=driver_id,
                driver_name=resolve_driver_name(driver_id) if driver_id else None,
                message=msg,
                process="engine",
            )
        )
    return rows


def merge_feed_rows(
    *groups: list[LogFeedRow],
    source_filter: LogFilter = "all",
    limit: int = 100,
) -> list[LogFeedRow]:
    merged: list[LogFeedRow] = []
    for group in groups:
        merged.extend(group)
    if source_filter != "all":
        merged = [r for r in merged if r.source == source_filter]
    merged.sort(key=lambda r: r.timestamp, reverse=True)
    return merged[:limit]


def infer_worker_status(ops_rows: list[LogFeedRow], *, now: datetime | None = None) -> dict[str, Any]:
    """Infer worker health from recent ops / ingestion-tagged rows."""
    now_utc = now or datetime.now(UTC)
    worker_ts: datetime | None = None
    for row in ops_rows:
        if row.process == "worker" or row.source == "ingestion":
            if worker_ts is None or row.timestamp > worker_ts:
                worker_ts = row.timestamp
    if worker_ts is None:
        return {
            "status": "unknown",
            "label": "Worker",
            "detail": "No recent worker events",
            "last_seen": None,
        }
    age = (now_utc - worker_ts).total_seconds()
    if age <= WORKER_STALE_SECONDS:
        status = "healthy"
        detail = f"Active ({int(age)}s ago)"
    else:
        status = "stale"
        detail = f"Stale ({int(age // 60)}m ago)"
    return {
        "status": status,
        "label": "Worker",
        "detail": detail,
        "last_seen": worker_ts.isoformat(),
    }
