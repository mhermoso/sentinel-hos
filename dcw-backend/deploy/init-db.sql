-- DCW PostgreSQL bootstrap — append-only enforcement for canonical_hos_logs.
-- Applied automatically on first container start via docker-entrypoint-initdb.d.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Block UPDATE and DELETE on the immutable HOS event store.
CREATE OR REPLACE FUNCTION dcw_block_canonical_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'canonical_hos_logs is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

-- Trigger is created after tables exist (init_db / first app start).
-- This function is ready for the Alembic migration to attach.
