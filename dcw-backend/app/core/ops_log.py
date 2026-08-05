"""Append-only JSONL sink for ``dcw.*`` operational log events.

Mirrors the alert-logger pattern: best-effort writes that never break the
pipeline, and a newest-first reader for the dashboard Logs page.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings

_bootstrap_logger = logging.getLogger("dcw.ops_log")
_HANDLER_ATTR = "_dcw_ops_handler_attached"


class OpsLogHandler(logging.Handler):
    """Write structured JSON lines for loggers under the ``dcw`` tree."""

    def __init__(self, path: Path, *, process_name: str) -> None:
        super().__init__(level=logging.INFO)
        self._path = path
        self._process_name = process_name

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("dcw"):
            return
        # Avoid recursive writes if file IO itself logs under dcw.*
        if record.name == "dcw.ops_log":
            return
        try:
            payload: dict[str, Any] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
                "process": self._process_name,
            }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, default=str) + "\n")
        except Exception:
            # Never break the application on log IO failures.
            self.handleError(record)


def configure_ops_log(*, process_name: str, path: Path | None = None) -> None:
    """Attach a single OpsLogHandler to the ``dcw`` logger (idempotent)."""
    dcw_logger = logging.getLogger("dcw")
    if getattr(dcw_logger, _HANDLER_ATTR, False):
        return

    log_path = path if path is not None else Path(settings.OPS_LOG_PATH)
    handler = OpsLogHandler(log_path, process_name=process_name)
    handler.setFormatter(logging.Formatter("%(message)s"))
    dcw_logger.addHandler(handler)
    # Ensure INFO+ from child loggers reaches the handler even if root is quieter.
    if dcw_logger.level == logging.NOTSET or dcw_logger.level > logging.INFO:
        dcw_logger.setLevel(logging.INFO)
    setattr(dcw_logger, _HANDLER_ATTR, True)


def read_ops_log(limit: int = 50, *, path: Path | None = None) -> list[dict[str, Any]]:
    """Read the newest ``limit`` JSONL records. Returns newest-first."""
    if limit < 1:
        return []
    log_path = path if path is not None else Path(settings.OPS_LOG_PATH)
    if not log_path.is_file():
        return []

    try:
        with log_path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        _bootstrap_logger.error("Failed to read ops log from %s: %s", log_path, exc)
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


def default_process_name() -> str:
    """Best-effort process label for ops log rows."""
    return os.environ.get("DCW_PROCESS_NAME") or f"pid-{os.getpid()}"
