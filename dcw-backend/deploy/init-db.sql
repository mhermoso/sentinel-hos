-- DCW PostgreSQL bootstrap — append-only enforcement for immutable stores.
-- Applied automatically on first container start via docker-entrypoint-initdb.d.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Block UPDATE and DELETE on the immutable HOS event store.
CREATE OR REPLACE FUNCTION dcw_block_canonical_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'canonical_hos_logs is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

-- Block UPDATE and DELETE on the GPS breadcrumb store (ADR-007).
CREATE OR REPLACE FUNCTION dcw_block_gps_breadcrumb_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'gps_breadcrumbs is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

-- Triggers are created after tables exist (init_db / first app start).
-- These functions are ready for the Alembic migration to attach.
