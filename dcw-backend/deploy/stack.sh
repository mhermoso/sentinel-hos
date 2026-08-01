#!/usr/bin/env bash
# Start the full DCW stack (API + worker) against running Postgres/Redis.
# Usage: deploy/stack.sh up | down | logs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

RUNTIME="${CONTAINER_RUNTIME:-podman}"
IMAGE="${DCW_IMAGE:-localhost/dcw-backend:latest}"
NETWORK="${DCW_NETWORK:-dcw-net}"
ENV_FILE="${ROOT_DIR}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ROOT_DIR/.env.example" "$ENV_FILE"
fi

POSTGRES_USER="${POSTGRES_USER:-dcw_user}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-dcw_secure_password}"
POSTGRES_DB="${POSTGRES_DB:-dcw_compliance_db}"
API_PORT="${API_PORT:-8000}"
ENVIRONMENT="${ENVIRONMENT:-production}"
DEBUG="${DEBUG:-false}"

ensure_network() {
    $RUNTIME network exists "$NETWORK" 2>/dev/null || \
        $RUNTIME network create "$NETWORK"
}

start_api() {
  $RUNTIME run -d \
    --name dcw-api \
    --replace \
  --network "$NETWORK" \
    -p "${API_PORT:-8000}:8000" \
    --env-file "$ENV_FILE" \
    -e POSTGRES_HOST=dcw-postgres \
    -e REDIS_HOST=dcw-redis \
    -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@dcw-postgres:5432/${POSTGRES_DB}" \
    -e REDIS_URL="redis://dcw-redis:6379/0" \
    -e ENVIRONMENT="${ENVIRONMENT:-production}" \
    -e DEBUG="${DEBUG:-false}" \
    "$IMAGE"
}

start_worker() {
  $RUNTIME run -d \
    --name dcw-worker \
    --replace \
    --network "$NETWORK" \
    --env-file "$ENV_FILE" \
    -e POSTGRES_HOST=dcw-postgres \
    -e REDIS_HOST=dcw-redis \
    -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@dcw-postgres:5432/${POSTGRES_DB}" \
    -e REDIS_URL="redis://dcw-redis:6379/0" \
    -e ENVIRONMENT="${ENVIRONMENT:-production}" \
    -e DEBUG="${DEBUG:-false}" \
    "$IMAGE" \
    arq app.domains.ingestion.poller.WorkerSettings
}

stop_stack() {
    $RUNTIME stop dcw-api dcw-worker 2>/dev/null || true
    $RUNTIME rm -f dcw-api dcw-worker 2>/dev/null || true
}

case "${1:-up}" in
    up)
        ensure_network
        bash "$SCRIPT_DIR/infra.sh" up
        sleep 3
        start_api
        start_worker
        echo "DCW stack running — API http://localhost:${API_PORT:-8000}/api/health"
        ;;
    down)
        stop_stack
        echo "DCW application containers stopped (infra still running — use deploy/infra.sh down)"
        ;;
    logs)
        $RUNTIME logs -f dcw-api dcw-worker
        ;;
    *)
        echo "Usage: $0 {up|down|logs}"
        exit 1
        ;;
esac
