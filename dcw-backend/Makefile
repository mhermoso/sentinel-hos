.PHONY: help dev worker test lint format clean db-up db-down \
        stack-up stack-down stack-logs image-build kube-up kube-down

PYTHON ?= python3
ARQ ?= arq
COMPOSE ?= podman compose
STACK_COMPOSE = $(COMPOSE) -f docker-compose.stack.yml
IMAGE_NAME ?= localhost/dcw-backend:latest

help:
	@echo "Driver Compliance Watch (DCW) SaaS Development Commands:"
	@echo "  make dev          Run FastAPI local development server"
	@echo "  make worker       Run ARQ background telematics ingestion worker"
	@echo "  make test         Execute full pytest suite"
	@echo "  make test-billing Test SaaS feature gates and tier enforcement"
	@echo "  make lint         Run ruff, mypy, and bandit security scans"
	@echo "  make format       Auto-format code using ruff"
	@echo "  make db-up        Spin up local PostgreSQL and Redis containers"
	@echo "  make db-down      Stop local infrastructure containers"
	@echo "  make stack-up     Build and run full stack (API + worker + DB + Redis)"
	@echo "  make stack-down   Stop full containerized stack"
	@echo "  make stack-logs   Tail logs from full stack"
	@echo "  make image-build  Build DCW container image"
	@echo "  make kube-up      Deploy via podman kube play (rootless)"
	@echo "  make kube-down    Tear down podman kube deployment"

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
	$(PYTHON) scripts/fetch_hos_history.py --days 10

backtest-event:
	$(PYTHON) scripts/backtest_alerts.py --mode event

backtest-event-export:
	$(PYTHON) scripts/backtest_alerts.py --mode event --csv --html

backtest-sweeper:
	$(PYTHON) scripts/backtest_alerts.py --mode sweeper

backtest-sweeper-export:
	$(PYTHON) scripts/backtest_alerts.py --mode sweeper --csv --html

test-alert:
	$(PYTHON) scripts/send_test_alert.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	rm -rf .coverage htmlcov/
