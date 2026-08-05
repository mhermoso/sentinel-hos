"""Unit tests for ops JSONL sink and Logs feed helpers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core import ops_log as ops_log_mod
from app.core.ops_log import OpsLogHandler, configure_ops_log, read_ops_log
from app.domains.dashboard.ops_feed import (
    LogFeedRow,
    infer_worker_status,
    merge_feed_rows,
    rows_from_ops,
)


def _detach_ops_handler() -> None:
    dcw = logging.getLogger("dcw")
    for handler in list(dcw.handlers):
        if isinstance(handler, OpsLogHandler):
            dcw.removeHandler(handler)
            handler.close()
    setattr(dcw, ops_log_mod._HANDLER_ATTR, False)


def test_ops_log_write_and_read_newest_first(tmp_path: Path) -> None:
    path = tmp_path / "ops-events.log"
    _detach_ops_handler()
    configure_ops_log(process_name="test-api", path=path)
    configure_ops_log(process_name="ignored", path=path)  # idempotent

    log = logging.getLogger("dcw.test.ops")
    log.setLevel(logging.INFO)
    log.propagate = True
    log.info("first event")
    log.info("second event")
    logging.getLogger("sqlalchemy.engine").info("should not appear")

    rows = read_ops_log(limit=10, path=path)
    _detach_ops_handler()
    assert len(rows) == 2
    assert rows[0]["message"] == "second event"
    assert rows[0]["process"] == "test-api"
    assert rows[0]["logger"] == "dcw.test.ops"
    assert rows[1]["message"] == "first event"


def test_ops_log_handler_skips_non_dcw(tmp_path: Path) -> None:
    path = tmp_path / "ops.log"
    handler = OpsLogHandler(path, process_name="unit")
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("other.system")
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.info("ignored")
    root.removeHandler(handler)
    assert read_ops_log(limit=5, path=path) == []


def test_read_ops_log_skips_bad_lines(tmp_path: Path) -> None:
    path = tmp_path / "ops.log"
    path.write_text(
        '{"timestamp":"t1","level":"INFO","logger":"dcw.a","message":"a","process":"api"}\n'
        "not-json\n"
        '{"timestamp":"t2","level":"INFO","logger":"dcw.b","message":"b","process":"worker"}\n',
        encoding="utf-8",
    )
    rows = read_ops_log(limit=2, path=path)
    assert [r["message"] for r in rows] == ["b", "a"]


def test_merge_feed_rows_filters_and_sorts() -> None:
    t0 = datetime(2026, 7, 31, 1, 0, tzinfo=UTC)
    t1 = datetime(2026, 7, 31, 2, 0, tzinfo=UTC)
    rows = merge_feed_rows(
        [
            LogFeedRow(t0, "system", "INFO", "", None, "sys"),
            LogFeedRow(t1, "ingestion", "INFO", "d1", "Driver", "ing"),
        ],
        source_filter="ingestion",
        limit=10,
    )
    assert len(rows) == 1
    assert rows[0].message == "ing"


def test_infer_worker_status_healthy_and_stale() -> None:
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    fresh = LogFeedRow(
        now - timedelta(seconds=30),
        "ingestion",
        "INFO",
        "",
        None,
        "poll ok",
        process="worker",
    )
    healthy = infer_worker_status([fresh], now=now)
    assert healthy["status"] == "healthy"

    old = LogFeedRow(
        now - timedelta(minutes=20),
        "ingestion",
        "INFO",
        "",
        None,
        "old poll",
        process="worker",
    )
    stale = infer_worker_status([old], now=now)
    assert stale["status"] == "stale"

    unknown = infer_worker_status([], now=now)
    assert unknown["status"] == "unknown"


def test_rows_from_ops_classifies_sources(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "ops.log"
    path.write_text(
        "\n".join(
            [
                '{"timestamp":"2026-07-31T12:00:00+00:00","level":"INFO","logger":"dcw.ingestion.poller","message":"poll","process":"worker"}',
                '{"timestamp":"2026-07-31T12:00:01+00:00","level":"INFO","logger":"dcw.engine.sweeper","message":"sweep","process":"worker"}',
                '{"timestamp":"2026-07-31T12:00:02+00:00","level":"INFO","logger":"dcw.notifier.subscriber","message":"sub","process":"api"}',
                '{"timestamp":"2026-07-31T12:00:03+00:00","level":"INFO","logger":"dcw.main","message":"boot","process":"api"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.domains.dashboard.ops_feed.read_ops_log",
        lambda limit=100: read_ops_log(limit=limit, path=path),
    )
    rows = rows_from_ops(limit=10)
    by_msg = {r.message: r.source for r in rows}
    assert by_msg["poll"] == "ingestion"
    assert by_msg["sweep"] == "engine"
    assert by_msg["sub"] == "alerts"
    assert by_msg["boot"] == "system"
