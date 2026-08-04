"""Helpers for Geotab DutyStatusLog edit supersession.

Geotab GetFeed re-emits the same DutyStatusLog ``id`` with an incremented
``version`` when a log is edited (status change, ``isIgnored``, inactive
``eventRecordStatus``, etc.). DCW stores these append-only; consumers must
keep only the newest version per ``raw_id`` before applying duty filters.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Sequence, TypeVar

T = TypeVar("T")


def _payload_dict(record: Any) -> dict[str, Any]:
    payload = getattr(record, "raw_payload", None)
    return payload if isinstance(payload, dict) else {}


def hos_record_version_key(record: Any) -> tuple[int, datetime]:
    """Return a comparable (provider_version, ingested_at) key for a HOS row."""
    payload = _payload_dict(record)
    version_raw = payload.get("version")
    try:
        version = int(version_raw) if version_raw is not None else -1
    except (TypeError, ValueError):
        version = -1

    ingested = getattr(record, "ingested_at", None)
    if not isinstance(ingested, datetime):
        ingested = datetime.min.replace(tzinfo=timezone.utc)
    elif ingested.tzinfo is None:
        ingested = ingested.replace(tzinfo=timezone.utc)
    else:
        ingested = ingested.astimezone(timezone.utc)
    return (version, ingested)


def select_latest_hos_versions(records: Sequence[T]) -> List[T]:
    """Keep the newest provider version per ``raw_id``; preserve timestamp order.

    Records without a ``raw_id`` are retained as-is (cannot be superseded).
    """
    latest: dict[str, T] = {}
    no_raw_id: list[T] = []

    for record in records:
        raw_id = getattr(record, "raw_id", None)
        if not raw_id:
            no_raw_id.append(record)
            continue
        key = str(raw_id)
        previous = latest.get(key)
        if previous is None or hos_record_version_key(record) > hos_record_version_key(
            previous
        ):
            latest[key] = record

    merged: list[T] = list(latest.values()) + no_raw_id

    def _sort_key(record: T) -> tuple[datetime, tuple[int, datetime]]:
        ts = getattr(record, "event_timestamp", None)
        if not isinstance(ts, datetime):
            ts = datetime.min.replace(tzinfo=timezone.utc)
        elif ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        return (ts, hos_record_version_key(record))

    merged.sort(key=_sort_key)
    return merged
