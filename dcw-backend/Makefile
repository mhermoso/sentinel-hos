.PHONY: help dev worker test lint format clean db-up db-down test-billing

PYTHON ?= python3
ARQ ?= arq

help:
@echo "Driver Compliance Watch (DCW) SaaS Development Commands:"
@echo "  make dev         Run FastAPI local development server"
@echo "  make worker      Run ARQ background telematics ingestion worker"
@echo "  make test        Execute full pytest suite"
@echo "  make test-billing Test SaaS feature gates and tier enforcement"
@echo "  make lint        Run ruff, mypy, and bandit security scans"
@echo "  make format      Auto-format code using ruff"
@echo "  make db-up       Spin up local PostgreSQL and Redis containers via Podman/Docker"
@echo "  make db-down     Stop and remove local infrastructure containers"

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
podman compose up -d || docker compose up -d

db-down:
podman compose down || docker compose down

clean:
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type d -name ".pytest_cache" -exec rm -rf {} +
find . -type d -name ".mypy_cache" -exec rm -rf {} +
rm -rf .coverage htmlcov/
