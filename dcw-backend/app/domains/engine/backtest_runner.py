"""Historical compliance alert backtest — shared by CLI and worker seed."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.security import compute_inputs_hash
from app.domains.engine.replay import compute_weekly_duty_seconds, logs_to_timeline_events
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline
from app.domains.ingestion.schemas import DCWCanonicalHOSLog
from app.domains.notifier.backtest_lock import InMemoryAlertLock


def backtest_dispatches_key(tenant_id: str) -> str:
    """Redis key for dashboard backtest dispatch JSON payload."""
    return f"backtest:dispatches:{tenant_id}"


def bootstrap_backtest_key(tenant_id: str, days: int) -> str:
    """Redis NX flag: set when N-day backtest seed has completed."""
    return f"bootstrap:backtest-dispatches:{days}d:v1:{tenant_id}"


def build_driver_name_map(
    grouped: dict[str, list[DCWCanonicalHOSLog]],
    *,
    redis_names: dict[str, str] | None = None,
) -> dict[str, str | None]:
    """Resolve driver_id → display name from logs and optional Redis cache."""
    names: dict[str, str | None] = {}
    redis_names = redis_names or {}
    for driver_id, logs in grouped.items():
        driver_name: str | None = redis_names.get(driver_id)
        if not driver_name:
            for log in logs:
                if log.driver_name:
                    driver_name = log.driver_name
                    break
        names[driver_id] = driver_name
    return names


def _evaluation_points_event(events: list[DriverTimeline.HOSEvent]) -> list[datetime]:
    return sorted({e.timestamp for e in events})


def _evaluation_points_sweeper(
    start: datetime,
    end: datetime,
    interval_seconds: int,
) -> list[datetime]:
    points: list[datetime] = []
    cursor = start
    while cursor <= end:
        points.append(cursor)
        cursor += timedelta(seconds=interval_seconds)
    return points


def _shift_id(as_of: datetime) -> str:
    return as_of.astimezone(UTC).strftime("%Y%m%d")


def run_backtest(
    grouped: dict[str, list[DCWCanonicalHOSLog]],
    mode: str,
    interval_seconds: int,
    *,
    driver_names: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Replay HOS timelines and simulate alert-lock dispatch deduplication."""
    pack = RulePack(version=settings.DEFAULT_RULE_PACK_VERSION)
    lock = InMemoryAlertLock()
    resolved_names = driver_names or build_driver_name_map(grouped)

    raw_violations: list[dict[str, Any]] = []
    dispatch_events: list[dict[str, Any]] = []
    raw_counter: Counter[str] = Counter()
    dispatch_counter: Counter[str] = Counter()
    driver_dispatch_counts: Counter[str] = Counter()
    driver_raw_counts: Counter[str] = Counter()

    all_timestamps: list[datetime] = []
    tenant_id = settings.GEOTAB_DATABASE or "unknown"

    for driver_id, logs in grouped.items():
        if not logs:
            continue
        tenant_id = logs[0].tenant_id
        events = logs_to_timeline_events(logs)
        if not events:
            continue

        timeline = DriverTimeline(driver_id=driver_id, tenant_id=tenant_id, events=events)
        all_timestamps.extend(e.timestamp for e in events)

        if mode == "event":
            eval_points = _evaluation_points_event(events)
        else:
            start = min(e.timestamp for e in events)
            end = max(e.timestamp for e in events)
            eval_points = _evaluation_points_sweeper(start, end, interval_seconds)

        for as_of in eval_points:
            weekly = compute_weekly_duty_seconds(
                events,
                as_of=as_of,
                cycle_days=settings.WEEKLY_CYCLE_DAYS,
            )
            inputs_hash = compute_inputs_hash(
                {
                    "tenant_id": tenant_id,
                    "driver_id": driver_id,
                    "as_of": as_of.isoformat(),
                    "event_count": len(events),
                }
            )
            result = pack.evaluate(
                timeline,
                inputs_hash=inputs_hash,
                weekly_duty_seconds=weekly,
                as_of=as_of,
            )

            for violation in result.violations:
                key = f"{violation.violation_type.value}:{violation.severity.value}"
                raw_counter[key] += 1
                driver_raw_counts[driver_id] += 1
                raw_violations.append(
                    {
                        "driver_id": driver_id,
                        "driver_name": resolved_names.get(driver_id),
                        "as_of": as_of.isoformat(),
                        "violation_type": violation.violation_type.value,
                        "severity": violation.severity.value,
                        "description": violation.description,
                        "rule_ref": violation.rule_ref,
                    }
                )

                shift = _shift_id(as_of)
                if lock.would_dispatch(
                    tenant_id,
                    driver_id,
                    shift,
                    violation.violation_type.value,
                    violation.severity.value,
                ):
                    dispatch_counter[key] += 1
                    driver_dispatch_counts[driver_id] += 1
                    dispatch_events.append(
                        {
                            "driver_id": driver_id,
                            "driver_name": resolved_names.get(driver_id),
                            "as_of": as_of.isoformat(),
                            "violation_type": violation.violation_type.value,
                            "severity": violation.severity.value,
                            "rule_ref": violation.rule_ref,
                            "description": violation.description,
                        }
                    )

    date_range: dict[str, str] = {}
    if all_timestamps:
        date_range = {
            "from": min(all_timestamps).isoformat(),
            "to": max(all_timestamps).isoformat(),
        }

    return {
        "meta": {
            "mode": mode,
            "interval_seconds": interval_seconds if mode == "sweeper" else None,
            "rule_pack_version": settings.DEFAULT_RULE_PACK_VERSION,
            "tenant_id": tenant_id,
            "driver_count": len(grouped),
            "total_events": sum(len(v) for v in grouped.values()),
            "date_range": date_range,
            "generated_at": datetime.now(UTC).isoformat(),
        },
        "summary": {
            "raw_violation_count": sum(raw_counter.values()),
            "would_dispatch_count": sum(dispatch_counter.values()),
            "by_rule_severity_raw": dict(raw_counter),
            "by_rule_severity_dispatch": dict(dispatch_counter),
            "top_drivers_by_dispatch": driver_dispatch_counts.most_common(10),
            "driver_dispatch_counts": dict(driver_dispatch_counts),
            "driver_raw_counts": dict(driver_raw_counts),
            "driver_names": resolved_names,
        },
        "dispatch_events": dispatch_events,
        "raw_violations": raw_violations,
        "sample_dispatches": dispatch_events[:50],
        "raw_violations_sample": raw_violations[:100],
    }


def serialize_dispatch_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Build the dashboard / Redis JSON shape for would-dispatch markers."""
    return {
        "meta": result["meta"],
        "summary": {
            "raw_violation_count": result["summary"]["raw_violation_count"],
            "would_dispatch_count": result["summary"]["would_dispatch_count"],
        },
        "dispatches": result.get("dispatch_events", []),
    }
