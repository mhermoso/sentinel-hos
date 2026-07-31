.PHONY: help dev worker test lint format clean db-up db-down \
        stack-up stack-down stack-logs image-build kube-up kube-down ui \
        seed-history seed-history-refresh backtest-dispatches remap-pc-ym \
        fetch-history

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)
ARQ ?= arq
COMPOSE ?= podman compose
STACK_COMPOSE = $(COMPOSE) -f docker-compose.stack.yml
IMAGE_NAME ?= localhost/dcw-backend:latest

# Historical HOS seed / backtest window (override: make fetch-history DAYS=14)
DAYS ?= 30
HISTORY_JSON ?= data/hos_$(DAYS)d_canonical.json

help:
	@echo "Driver Compliance Watch (DCW) SaaS Development Commands:"
	@echo "  make dev          Run FastAPI local development server"
	@echo "  make worker       Run ARQ background telematics ingestion worker"
	@echo "  make ui           Print HOS timeline dashboard URL (http://localhost:8000/ui)"
	@echo "  make test         Execute full pytest suite"
	@echo "  make test-billing Test SaaS feature gates and tier enforcement"
	@echo "  make lint         Run ruff, mypy, and bandit security scans"
	@echo "  make format       Auto-format code using ruff"
	@echo "  make db-up        Spin up local PostgreSQL and Redis containers"
	@echo "  make db-down      Stop local infrastructure containers"
	@echo "  make fetch-history  Fetch last DAYS (default 30) Geotab HOS → HISTORY_JSON"
	@echo "  make seed-history Persist $(HISTORY_JSON) into Postgres"
	@echo "  make seed-history-refresh  DEV: remap PC/YM, truncate tenant HOS, re-seed"
	@echo "  make remap-pc-ym  Remap PC/YM in $(HISTORY_JSON) (no Geotab)"
	@echo "  make backtest-dispatches  Run event backtest → data/backtest_dispatches.json"
	@echo "  make stack-up     Build and run full stack (API + worker + DB + Redis)"
	@echo "  make stack-down   Stop full containerized stack"
	@echo "  make stack-logs   Tail logs from full stack"
	@echo "  make image-build  Build DCW container image"
	@echo "  make kube-up      Deploy via podman kube play (rootless)"
	@echo "  make kube-down    Tear down podman kube deployment"

ui:
	@echo "HOS timeline dashboard: http://localhost:8000/ui"
	@echo "  1. make db-up"
	@echo "  2. make fetch-history   # or: make seed-history if $(HISTORY_JSON) exists"
	@echo "  3. make seed-history-refresh   # DEV: truncate + re-seed"
	@echo "  4. make backtest-dispatches"
	@echo "  5. ALERT_DRY_RUN=true make dev"
	@echo "  6. ALERT_DRY_RUN=true make worker   # optional live Geotab poll"

seed-history:
	$(PYTHON) scripts/fetch_hos_history.py --from-file $(HISTORY_JSON) --persist

remap-pc-ym:
	$(PYTHON) scripts/remap_canonical_pc_ym.py $(HISTORY_JSON)

# DEV-ONLY: remaps PC/YM in JSON, truncates tenant HOS/audit rows, re-seeds Postgres.
seed-history-refresh: remap-pc-ym
	$(PYTHON) scripts/seed_history_refresh.py --from-file $(HISTORY_JSON)

backtest-dispatches:
	$(PYTHON) scripts/backtest_alerts.py --mode event --input $(HISTORY_JSON)

dev:
	$(PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	$(ARQ) app.domains.ingestion.poller.WorkerSettings

test:
	pytest tests/ -v --cov=app --cov-report=term-missing

test-billing:
	pytest tests/unit/test_billing_gates.py -v

lint:
	ruff check app/ tests/
	mypy app/
	bandit -r app/ -c pyproject.toml

format:
	ruff check --fix app/ tests/
	ruff format app/ tests/

db-up:
	$(COMPOSE) up -d 2>/dev/null || bash deploy/infra.sh up

db-down:
	$(COMPOSE) down 2>/dev/null || bash deploy/infra.sh down

stack-up:
	@test -f .env || cp .env.example .env
	$(STACK_COMPOSE) up -d --build 2>/dev/null || (make image-build && bash deploy/stack.sh up)

stack-down:
	$(STACK_COMPOSE) down 2>/dev/null || bash deploy/stack.sh down

stack-logs:
	$(STACK_COMPOSE) logs -f 2>/dev/null || bash deploy/stack.sh logs

image-build:
	podman build -t $(IMAGE_NAME) -f Containerfile . || docker build -t $(IMAGE_NAME) -f Containerfile .

kube-up: image-build
	podman kube play deploy/dcw-stack.yaml

kube-down:
	podman kube down deploy/dcw-stack.yaml

fetch-history:
	$(PYTHON) scripts/fetch_hos_history.py --days $(DAYS) --output $(HISTORY_JSON)

backtest-event:
	$(PYTHON) scripts/backtest_alerts.py --mode event --input $(HISTORY_JSON)

backtest-event-export:
	$(PYTHON) scripts/backtest_alerts.py --mode event --csv --html --input $(HISTORY_JSON)

backtest-sweeper:
	$(PYTHON) scripts/backtest_alerts.py --mode sweeper --input $(HISTORY_JSON)

backtest-sweeper-export:
	$(PYTHON) scripts/backtest_alerts.py --mode sweeper --csv --html --input $(HISTORY_JSON)

test-alert:
	$(PYTHON) scripts/send_test_alert.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	rm -rf .coverage htmlcov/
