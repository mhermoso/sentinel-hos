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

## Historical alert backtest (10-day Geotab)

Dry-run pipeline to count how many alerts would have fired — no UI required; reports are written to `reports/`.

```bash
# 1. Fetch last 10 days from Geotab (uses .env credentials)
make fetch-history

# 2. Pass 1 — evaluate at each HOS status change (fast)
make backtest-event

# 3. Pass 2 — evaluate every 120s (production sweeper cadence)
make backtest-sweeper

# Optional: CSV + HTML for spreadsheet / browser review
make backtest-event-export
# or: python scripts/backtest_alerts.py --mode event --csv --html

Rule pack `fmcsa-us-property@1.3.0` credits valid 34-hour OFF/SB restarts in weekly duty totals (two 1–5 AM periods in `DEFAULT_HOME_TERMINAL_TIMEZONE`, default US Central).

# Optional: load history into Postgres for live sweeper testing
python scripts/fetch_hos_history.py --days 10 --persist
```

Outputs:
- `data/hos_10d_canonical.json` — canonical logs grouped by driver
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
