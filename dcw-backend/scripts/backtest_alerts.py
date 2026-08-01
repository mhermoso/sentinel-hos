#!/usr/bin/env python3
"""Historical compliance alert backtest — event-boundary or sweeper-interval replay."""

from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import settings
from app.core.security import compute_inputs_hash
from app.domains.engine.replay import compute_weekly_duty_seconds, logs_to_timeline_events
from app.domains.engine.rule_pack import RulePack
from app.domains.engine.schemas import DriverTimeline, Violation
from app.domains.ingestion.schemas import DCWCanonicalHOSLog
from app.domains.notifier.backtest_lock import InMemoryAlertLock

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dcw.scripts.backtest_alerts")


def load_grouped_json(path: Path) -> Dict[str, List[DCWCanonicalHOSLog]]:
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    grouped: Dict[str, List[DCWCanonicalHOSLog]] = {}
    for driver_id, records in raw.items():
        grouped[driver_id] = [DCWCanonicalHOSLog.model_validate(r) for r in records]
    return grouped


def build_driver_name_map(
    grouped: Dict[str, List[DCWCanonicalHOSLog]],
) -> Dict[str, str | None]:
    """Resolve driver_id → display name from the first log record that carries one."""
    names: Dict[str, str | None] = {}
    for driver_id, logs in grouped.items():
        driver_name: str | None = None
        for log in logs:
            if log.driver_name:
                driver_name = log.driver_name
                break
        names[driver_id] = driver_name
    return names


def _evaluation_points_event(events: List[DriverTimeline.HOSEvent]) -> List[datetime]:
    return sorted({e.timestamp for e in events})


def _evaluation_points_sweeper(
    start: datetime,
    end: datetime,
    interval_seconds: int,
) -> List[datetime]:
    points: List[datetime] = []
    cursor = start
    while cursor <= end:
        points.append(cursor)
        cursor += timedelta(seconds=interval_seconds)
    return points


def _shift_id(as_of: datetime) -> str:
    return as_of.astimezone(timezone.utc).strftime("%Y%m%d")


def run_backtest(
    grouped: Dict[str, List[DCWCanonicalHOSLog]],
    mode: str,
    interval_seconds: int,
) -> Dict[str, Any]:
    pack = RulePack(version=settings.DEFAULT_RULE_PACK_VERSION)
    lock = InMemoryAlertLock()
    driver_names = build_driver_name_map(grouped)

    raw_violations: List[Dict[str, Any]] = []
    dispatch_events: List[Dict[str, Any]] = []
    raw_counter: Counter[str] = Counter()
    dispatch_counter: Counter[str] = Counter()
    driver_dispatch_counts: Counter[str] = Counter()
    driver_raw_counts: Counter[str] = Counter()

    all_timestamps: List[datetime] = []
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
                        "driver_name": driver_names.get(driver_id),
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
                            "driver_name": driver_names.get(driver_id),
                            "as_of": as_of.isoformat(),
                            "violation_type": violation.violation_type.value,
                            "severity": violation.severity.value,
                            "rule_ref": violation.rule_ref,
                            "description": violation.description,
                        }
                    )

    date_range = {}
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
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "summary": {
            "raw_violation_count": sum(raw_counter.values()),
            "would_dispatch_count": sum(dispatch_counter.values()),
            "by_rule_severity_raw": dict(raw_counter),
            "by_rule_severity_dispatch": dict(dispatch_counter),
            "top_drivers_by_dispatch": driver_dispatch_counts.most_common(10),
            "driver_dispatch_counts": dict(driver_dispatch_counts),
            "driver_raw_counts": dict(driver_raw_counts),
            "driver_names": driver_names,
        },
        "dispatch_events": dispatch_events,
        "raw_violations": raw_violations,
        "sample_dispatches": dispatch_events[:50],
        "raw_violations_sample": raw_violations[:100],
    }


def _report_stem(ts: str, mode: str) -> str:
    return f"alert-backtest-{ts}-{mode}"


def write_csv_reports(result: Dict[str, Any], reports_dir: Path, ts: str) -> List[Path]:
    """Write CSV exports for spreadsheet review."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    mode = result["meta"]["mode"]
    stem = _report_stem(ts, mode)
    written: List[Path] = []

    dispatches = result.get("dispatch_events", [])
    if dispatches:
        path = reports_dir / f"{stem}-dispatches.csv"
        fields = [
            "as_of",
            "driver_id",
            "driver_name",
            "violation_type",
            "severity",
            "rule_ref",
            "description",
        ]
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(dispatches)
        written.append(path)

    raw_rows = result.get("raw_violations", [])
    if raw_rows:
        path = reports_dir / f"{stem}-raw-violations.csv"
        fields = [
            "as_of",
            "driver_id",
            "driver_name",
            "violation_type",
            "severity",
            "rule_ref",
            "description",
        ]
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(raw_rows)
        written.append(path)

    summary = result["summary"]
    rule_keys = sorted(
        set(summary["by_rule_severity_raw"]) | set(summary["by_rule_severity_dispatch"])
    )
    if rule_keys:
        path = reports_dir / f"{stem}-summary-by-rule.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["rule_severity", "raw_count", "would_dispatch_count"])
            for key in rule_keys:
                writer.writerow([
                    key,
                    summary["by_rule_severity_raw"].get(key, 0),
                    summary["by_rule_severity_dispatch"].get(key, 0),
                ])
        written.append(path)

    driver_disp = summary.get("driver_dispatch_counts", {})
    driver_raw = summary.get("driver_raw_counts", {})
    driver_names = summary.get("driver_names", {})
    all_drivers = sorted(set(driver_disp) | set(driver_raw))
    if all_drivers:
        path = reports_dir / f"{stem}-by-driver.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "driver_id",
                "driver_name",
                "raw_violation_count",
                "would_dispatch_count",
            ])
            for driver_id in all_drivers:
                writer.writerow([
                    driver_id,
                    driver_names.get(driver_id) or "",
                    driver_raw.get(driver_id, 0),
                    driver_disp.get(driver_id, 0),
                ])
        written.append(path)

    return written


def _html_table(headers: List[str], rows: List[List[Any]], table_id: str) -> str:
    """Build a sortable HTML table."""
    thead = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(c))}</td>" for c in row)
        body_rows.append(f"<tr>{cells}</tr>")
    tbody = "\n".join(body_rows)
    return f"""
<table class="sortable" id="{table_id}">
  <thead><tr>{thead}</tr></thead>
  <tbody>
{tbody}
  </tbody>
</table>"""


def write_html_report(result: Dict[str, Any], reports_dir: Path, ts: str) -> Path:
    """Write a self-contained HTML report with sortable tables."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    meta = result["meta"]
    summary = result["summary"]
    mode = meta["mode"]
    stem = _report_stem(ts, mode)
    path = reports_dir / f"{stem}.html"

    summary_rows = [
        ["Raw violations (all evaluation points)", summary["raw_violation_count"]],
        ["Would dispatch (after alert-lock dedup)", summary["would_dispatch_count"]],
    ]

    rule_keys = sorted(
        set(summary["by_rule_severity_raw"]) | set(summary["by_rule_severity_dispatch"])
    )
    rule_rows = [
        [
            key,
            summary["by_rule_severity_raw"].get(key, 0),
            summary["by_rule_severity_dispatch"].get(key, 0),
        ]
        for key in rule_keys
    ]

    driver_disp = summary.get("driver_dispatch_counts", {})
    driver_raw = summary.get("driver_raw_counts", {})
    driver_names = summary.get("driver_names", {})
    driver_rows = sorted(
        [
            (
                d,
                driver_names.get(d) or "",
                driver_raw.get(d, 0),
                driver_disp.get(d, 0),
            )
            for d in set(driver_disp) | set(driver_raw)
        ],
        key=lambda r: (-r[3], -r[2], r[0]),
    )

    dispatch_rows = [
        [
            r["as_of"],
            r["driver_id"],
            r.get("driver_name") or "",
            r["violation_type"],
            r["severity"],
            r.get("rule_ref", ""),
            r["description"],
        ]
        for r in result.get("dispatch_events", [])
    ]

    interval_note = (
        f" (interval {meta['interval_seconds']}s)"
        if meta.get("interval_seconds")
        else ""
    )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>DCW Alert Backtest — {html.escape(mode)}{interval_note}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}
    h1 {{ font-size: 1.5rem; }}
    h2 {{ margin-top: 2rem; font-size: 1.15rem; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; }}
    .meta {{ color: #555; font-size: 0.9rem; line-height: 1.6; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.85rem; }}
    th, td {{ border: 1px solid #ddd; padding: 0.45rem 0.6rem; text-align: left; }}
    th {{ background: #f4f4f5; cursor: pointer; user-select: none; }}
    th:hover {{ background: #e8e8ea; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    .hint {{ font-size: 0.8rem; color: #666; }}
  </style>
</head>
<body>
  <h1>DCW Alert Backtest Report</h1>
  <div class="meta">
    <p><strong>Generated:</strong> {html.escape(meta["generated_at"])}</p>
    <p><strong>Mode:</strong> {html.escape(mode)}{html.escape(interval_note)}</p>
    <p><strong>Rule pack:</strong> {html.escape(meta["rule_pack_version"])}</p>
    <p><strong>Tenant:</strong> {html.escape(meta["tenant_id"])}</p>
    <p><strong>Drivers:</strong> {meta["driver_count"]} &nbsp;|&nbsp;
       <strong>HOS events:</strong> {meta["total_events"]}</p>
    <p><strong>Date range:</strong> {html.escape(meta["date_range"].get("from", "n/a"))}
       → {html.escape(meta["date_range"].get("to", "n/a"))}</p>
  </div>

  <h2>Summary</h2>
  <p class="hint">Click column headers to sort.</p>
  {_html_table(["Metric", "Count"], summary_rows, "tbl-summary")}

  <h2>By rule / severity</h2>
  {_html_table(["Rule", "Raw count", "Would dispatch"], rule_rows, "tbl-rules")}

  <h2>By driver</h2>
  {_html_table(
      ["Driver ID", "Driver name", "Raw violations", "Would dispatch"],
      driver_rows,
      "tbl-drivers",
  )}

  <h2>All would-dispatch events ({len(dispatch_rows)})</h2>
  {_html_table(
      ["Time (UTC)", "Driver ID", "Driver name", "Type", "Severity", "Rule", "Description"],
      dispatch_rows,
      "tbl-dispatches",
  )}

  <script>
    document.querySelectorAll("table.sortable").forEach((table) => {{
      table.querySelectorAll("th").forEach((th, colIndex) => {{
        th.addEventListener("click", () => {{
          const tbody = table.querySelector("tbody");
          const rows = Array.from(tbody.querySelectorAll("tr"));
          const asc = th.dataset.sortDir !== "asc";
          table.querySelectorAll("th").forEach((h) => delete h.dataset.sortDir);
          th.dataset.sortDir = asc ? "asc" : "desc";
          rows.sort((a, b) => {{
            const av = a.children[colIndex].textContent.trim();
            const bv = b.children[colIndex].textContent.trim();
            const an = parseFloat(av), bn = parseFloat(bv);
            const cmp = (!isNaN(an) && !isNaN(bn)) ? an - bn : av.localeCompare(bv);
            return asc ? cmp : -cmp;
          }});
          rows.forEach((r) => tbody.appendChild(r));
        }});
      }});
    }});
  </script>
</body>
</html>
"""

    path.write_text(doc, encoding="utf-8")
    return path


def write_reports(
    result: Dict[str, Any],
    reports_dir: Path,
    *,
    export_csv: bool = False,
    export_html: bool = False,
) -> Tuple[Path, Path, List[Path], Path | None]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = result["meta"]["mode"]
    json_path = reports_dir / f"alert-backtest-{ts}.json"
    md_path = reports_dir / f"alert-backtest-{ts}.md"

    json_payload = {
        **result,
        "dispatch_events": result.get("sample_dispatches", []),
        "raw_violations": result.get("raw_violations_sample", []),
    }
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(json_payload, fh, indent=2)

    meta = result["meta"]
    summary = result["summary"]
    lines = [
        "# DCW Alert Backtest Report",
        "",
        f"- **Generated:** {meta['generated_at']}",
        f"- **Mode:** {meta['mode']}",
        f"- **Rule pack:** {meta['rule_pack_version']}",
        f"- **Tenant:** {meta['tenant_id']}",
        f"- **Drivers:** {meta['driver_count']}",
        f"- **Total HOS events:** {meta['total_events']}",
        f"- **Date range:** {meta['date_range'].get('from', 'n/a')} → {meta['date_range'].get('to', 'n/a')}",
        "",
        "## Violation counts",
        "",
        "| Metric | Count |",
        "|--------|------:|",
        f"| Raw violations (all evaluation points) | {summary['raw_violation_count']} |",
        f"| Would dispatch (after alert-lock dedup) | {summary['would_dispatch_count']} |",
        "",
        "## Raw violations by rule / severity",
        "",
        "| Rule | Count |",
        "|------|------:|",
    ]
    for key, count in sorted(summary["by_rule_severity_raw"].items()):
        lines.append(f"| {key} | {count} |")

    lines.extend(
        [
            "",
            "## Would-dispatch by rule / severity",
            "",
            "| Rule | Count |",
            "|------|------:|",
        ]
    )
    for key, count in sorted(summary["by_rule_severity_dispatch"].items()):
        lines.append(f"| {key} | {count} |")

    driver_names = summary.get("driver_names", {})
    lines.extend(["", "## Top drivers by dispatch count", ""])
    for driver_id, count in summary["top_drivers_by_dispatch"]:
        name = driver_names.get(driver_id)
        label = f"{name} (`{driver_id}`)" if name else f"`{driver_id}`"
        lines.append(f"- {label}: {count}")

    lines.extend(["", "## Sample dispatch events", ""])
    for row in result["sample_dispatches"][:20]:
        name = row.get("driver_name")
        driver_label = f"{name} (`{row['driver_id']}`)" if name else f"`{row['driver_id']}`"
        lines.append(
            f"- **{row['as_of']}** driver {driver_label} "
            f"{row['violation_type']} ({row['severity']}): {row['description'][:80]}"
        )

    csv_paths: List[Path] = []
    html_path: Path | None = None
    if export_csv:
        csv_paths = write_csv_reports(result, reports_dir, ts)
    if export_html:
        html_path = write_html_report(result, reports_dir, ts)

    if csv_paths or html_path:
        stem = _report_stem(ts, mode)
        lines.extend(["", "## Export files", ""])
        for csv_path in csv_paths:
            lines.append(f"- CSV: `{csv_path.name}`")
        if html_path:
            lines.append(f"- HTML: `{html_path.name}`")

    with md_path.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    return json_path, md_path, csv_paths, html_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest compliance alerts on historical HOS data")
    parser.add_argument(
        "--input",
        type=Path,
        default=_ROOT / "data" / "hos_10d_canonical.json",
        help="Canonical JSON grouped by driver_id",
    )
    parser.add_argument(
        "--mode",
        choices=["event", "sweeper"],
        default="event",
        help="event = evaluate at each status change; sweeper = fixed interval",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=settings.POLL_INTERVAL_SECONDS,
        help="Sweeper interval in seconds (sweeper mode only)",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=_ROOT / "reports",
        help="Directory for markdown + JSON reports",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Export full results as CSV files (dispatches, violations, summary)",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Export self-contained HTML report with sortable tables",
    )
    args = parser.parse_args()

    if not args.input.exists():
        logger.error("Input file not found: %s — run scripts/fetch_hos_history.py first", args.input)
        sys.exit(1)

    grouped = load_grouped_json(args.input)
    logger.info("Loaded %d drivers from %s", len(grouped), args.input)

    result = run_backtest(grouped, mode=args.mode, interval_seconds=args.interval)
    json_path, md_path, csv_paths, html_path = write_reports(
        result,
        args.reports_dir,
        export_csv=args.csv,
        export_html=args.html,
    )

    logger.info(
        "Backtest complete: raw=%d would_dispatch=%d",
        result["summary"]["raw_violation_count"],
        result["summary"]["would_dispatch_count"],
    )
    logger.info("Reports: %s , %s", md_path, json_path)
    for csv_path in csv_paths:
        logger.info("CSV: %s", csv_path)
    if html_path:
        logger.info("HTML: %s", html_path)


if __name__ == "__main__":
    main()
