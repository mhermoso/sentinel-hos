# Driver Compliance Watch (DCW) — Managed SaaS Platform

Driver Compliance Watch (DCW) is a deterministic 49 CFR Part 395 Hours of Service (HOS) compliance evaluation engine and real-time fleet monitoring platform.

## Architecture

The system follows the five-layer architecture defined in [`../end-to-end.md`](../end-to-end.md):

| Layer | Module | Responsibility |
|-------|--------|----------------|
| 1 | `domains/ingestion` | ARQ poller, Geotab/Motive/Samsara adapters, normalizer |
| 2 | `core/database` + `core/redis` | PostgreSQL 16 append-only store, Redis 7.2 cache/pub-sub |
| 3 | `domains/engine` | Rule pack engine, state machine, compliance sweeper |
| 4 | `domains/notifier` | Redis subscriber, Twilio Voice IVR + SMS |
| 5 | `domains/dashboard` | FastAPI REST API for live status and audit queries |

## Quickstart (local development)

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your Geotab/Twilio credentials

# 2. Start PostgreSQL 16 + Redis 7.2
make db-up

# 3. Install dependencies (Python 3.12+)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 4. Run API server (includes notifier subscriber)
make dev

# 5. In a second terminal, run the ARQ ingestion worker
make worker
```

API docs: http://localhost:8000/docs  
Health check: http://localhost:8000/api/health  
HOS timeline UI: http://localhost:8000/ui  (`make ui` prints the runbook)

## Full containerized deployment

### Option A: Docker/Podman Compose (recommended for dev/staging)

```bash
cp .env.example .env
# Edit .env with production secrets

make stack-up      # builds image + starts API, worker, Postgres, Redis
make stack-logs    # tail all service logs
make stack-down    # tear down
```

### Option B: Podman Kube (rootless, per ADR-001)

```bash
make image-build   # builds localhost/dcw-backend:latest
make kube-up       # podman kube play deploy/dcw-stack.yaml
make kube-down     # podman kube down deploy/dcw-stack.yaml
```

## Service topology

```
┌─────────────┐     ┌─────────────┐
│  dcw-api    │     │ dcw-worker  │
│  (FastAPI)  │     │   (ARQ)     │
│  + notifier │     │  ingestion  │
│  subscriber │     │  + sweeper  │
└──────┬──────┘     └──────┬──────┘
       │                   │
       └────────┬──────────┘
                ▼
    ┌───────────────────────┐
    │  PostgreSQL 16        │
    │  Redis 7.2            │
    └───────────────────────┘
```

## Features

- **Deterministic Math Engine**: 0% probabilistic or LLM logic in regulatory evaluations.
- **Multi-Tenant SaaS**: Built with single-tenant isolation and per-vehicle/per-month (PVPM) tier gating.
- **Multi-Provider Telematics**: Ingests feeds from Geotab, Motive, and Samsara.
- **Automated Telephony**: Outbound Twilio Voice IVR and SMS warnings for shift violations.

## Testing

```bash
make test          # full suite
make test-billing  # SaaS tier gate tests
make lint          # ruff + mypy + bandit
```

## HOS timeline dashboard (HTMX)

Geotab-style daily OFF/SB/D/ON status grid with alert markers. Same UI for historical seed data and live Geotab ingestion. Display timezone defaults to **Central** (`America/Chicago`); change it from the **Local time** selector in the header (saved in a cookie). Engine 34h restart still uses `DEFAULT_HOME_TERMINAL_TIMEZONE`.

**Personal conveyance (PC) / yard move (YM):** Geotab sends these as duty-status values (`PC`, `INT_PC`, `YM`, …). The adapter maps them to canonical `PC` / `YM`. On the day grid, PC is a striped band on the **OFF** lane and YM on the **ON** lane (legend under the chart). In the engine, PC counts as off-duty rest; YM counts as yard-move (duty/driving).

```bash
# 1. Infra + seed Postgres from existing 30-day JSON (or fetch fresh below)
make db-up
make seed-history
# Fresh pull instead (default DAYS=30 → data/hos_30d_canonical.json):
#   make fetch-history
#   make seed-history-refresh   # DEV: remap + truncate tenant HOS + re-seed
# Override window/path: make fetch-history DAYS=14 HISTORY_JSON=data/hos_14d_canonical.json

# After a PC/YM mapping fix (or to repair UNKNOWN seed rows), refresh seed data:
#   make fetch-history          # optional: rewrite JSON from Geotab
#   make seed-history-refresh   # remap JSON + truncate tenant HOS + re-seed (DEV only)
#   make backtest-dispatches

# 2. Backtest → data/backtest_dispatches.json (UI alert markers)
make backtest-dispatches

# 3. API + UI (notifier dry-run — no Twilio)
# Ensure .env has ALERT_DRY_RUN=true (default) and DEFAULT_HOME_TERMINAL_TIMEZONE=America/Chicago
ALERT_DRY_RUN=true make dev

# 4. Optional: live Geotab poll + compliance sweeper (alerts log-only)
ALERT_DRY_RUN=true make worker
```

`make seed-history-refresh` is **dev-only**: it remaps `data/hos_30d_canonical.json` in place, deletes `canonical_hos_logs` / `audit_records` / `log_event_edits` for `GEOTAB_DATABASE`, then re-seeds. Append-only policy still applies to live production writes.

Open http://localhost:8000/ui — **Drivers** or **Alerts** tab. On a driver day, click a severity marker or list row for calculation details (limit gauges, shift window, causal HOS highlights). HTMX refreshes the driver list and current day every 45s. Tail dry-run alerts: `tail -f logs/compliance-alerts.log`.

Useful API:
- `GET /api/drivers` — all drivers (PG history ∪ Redis active)
- `GET /api/drivers/{id}/day?date=YYYY-MM-DD&tz=` — day grid, duration totals, merged markers
- `GET /api/alerts?severity=&from=&to=&driver_id=&source=` — fleet alert list
- `GET /api/drivers/{id}/alerts/detail?as_of=&violation_type=` — recompute clocks + explanation
- `GET /api/drivers/{id}/alert-markers?from=&to=` — backtest + live audit markers

## Historical alert backtest (30-day Geotab)

Dry-run pipeline to count how many alerts would have fired; reports go to `reports/`, and would-dispatch events are also written to `data/backtest_dispatches.json` for the dashboard.

```bash
# 1. Fetch last 30 days from Geotab (uses .env credentials)
make fetch-history

# 2. Pass 1 — evaluate at each HOS status change (fast); writes dashboard JSON
make backtest-event
# or: make backtest-dispatches

# 3. Pass 2 — evaluate every 120s (production sweeper cadence)
make backtest-sweeper

# Optional: CSV + HTML for spreadsheet / browser review
make backtest-event-export
# or: python scripts/backtest_alerts.py --mode event --csv --html

Rule pack `fmcsa-us-property@1.3.0` credits valid 34-hour OFF/SB restarts in weekly duty totals (two 1–5 AM periods in `DEFAULT_HOME_TERMINAL_TIMEZONE`, default US Central).

# Optional: load history into Postgres for dashboard / live sweeper testing
python scripts/fetch_hos_history.py --days 30 --persist
# or from an existing file (no Geotab call):
python scripts/fetch_hos_history.py --from-file data/hos_30d_canonical.json --persist
```

If Geotab auth fails, copy an existing seed (`cp data/hos_10d_canonical.json data/hos_30d_canonical.json`) and continue with `make seed-history` / `make backtest-dispatches`. Distances and ignore filtering still work; the window is just shorter until a successful fetch.

Outputs:
- `data/hos_30d_canonical.json` — canonical logs grouped by driver (default `DAYS=30`)
- `data/backtest_dispatches.json` — would-dispatch markers for `/ui` (always written unless `--no-dispatches-out`)
- `reports/alert-backtest-*.md` — human-readable summary
- `reports/alert-backtest-*.json` — machine-readable for diffs

With `--csv` and/or `--html`:
- `reports/alert-backtest-{ts}-{mode}-dispatches.csv` — all would-dispatch events (`driver_id`, `driver_name`, …)
- `reports/alert-backtest-{ts}-{mode}-raw-violations.csv` — every raw violation (`driver_id`, `driver_name`, …)
- `reports/alert-backtest-{ts}-{mode}-summary-by-rule.csv` — counts by rule/severity
- `reports/alert-backtest-{ts}-{mode}-by-driver.csv` — per-driver totals (`driver_id`, `driver_name`, counts)
- `reports/alert-backtest-{ts}-{mode}.html` — self-contained report with sortable tables (includes driver names)

Driver names come from `driver_name` on each `DCWCanonicalHOSLog` record (populated by `fetch_hos_history.py` via the Geotab Users API).

Live alert logging (dry-run by default, `ALERT_DRY_RUN=true`):

```bash
make test-alert
tail -f logs/compliance-alerts.log
```

Set `ALERT_DRY_RUN=false` and `TWILIO_TEST_TO_PHONE` / `TWILIO_TEST_DISPATCHER_PHONE` before placing real Twilio calls.
