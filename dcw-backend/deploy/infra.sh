#!/usr/bin/env bash
# Start PostgreSQL 16 and Redis 7.2 for local DCW development.
# Used as fallback when podman compose / docker compose is unavailable.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

POSTGRES_USER="${POSTGRES_USER:-dcw_user}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-dcw_secure_password}"
POSTGRES_DB="${POSTGRES_DB:-dcw_compliance_db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
REDIS_PORT="${REDIS_PORT:-6379}"

RUNTIME="${CONTAINER_RUNTIME:-podman}"
NETWORK="${DCW_NETWORK:-dcw-net}"

ensure_network() {
    $RUNTIME network exists "$NETWORK" 2>/dev/null || \
        $RUNTIME network create "$NETWORK"
}

start_postgres() {
    ensure_network
    if $RUNTIME ps -a --format '{{.Names}}' | grep -qx dcw-postgres; then
        $RUNTIME rm -f dcw-postgres 2>/dev/null || true
    fi
    $RUNTIME run -d \
        --name dcw-postgres \
        --network "$NETWORK" \
        -e POSTGRES_USER="$POSTGRES_USER" \
        -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
        -e POSTGRES_DB="$POSTGRES_DB" \
        -p "${POSTGRES_PORT}:5432" \
        -v dcw_pgdata:/var/lib/postgresql/data \
        -v "$ROOT_DIR/deploy/init-db.sql:/docker-entrypoint-initdb.d/01-init.sql:ro" \
        docker.io/library/postgres:16-alpine
}

start_redis() {
    ensure_network
    if $RUNTIME ps -a --format '{{.Names}}' | grep -qx dcw-redis; then
        $RUNTIME rm -f dcw-redis 2>/dev/null || true
    fi
    $RUNTIME run -d \
        --name dcw-redis \
        --network "$NETWORK" \
        -p "${REDIS_PORT}:6379" \
        -v dcw_redisdata:/data \
        docker.io/library/redis:7.2-alpine \
        redis-server --save 60 1 --loglevel warning
}

stop_all() {
    $RUNTIME stop dcw-postgres dcw-redis 2>/dev/null || true
    $RUNTIME rm -f dcw-postgres dcw-redis 2>/dev/null || true
}

case "${1:-up}" in
    up)
        start_postgres
        start_redis
        echo "DCW infrastructure ready: postgres=localhost:${POSTGRES_PORT} redis=localhost:${REDIS_PORT}"
        ;;
    down)
        stop_all
        echo "DCW infrastructure stopped"
        ;;
    *)
        echo "Usage: $0 {up|down}"
        exit 1
        ;;
esac
