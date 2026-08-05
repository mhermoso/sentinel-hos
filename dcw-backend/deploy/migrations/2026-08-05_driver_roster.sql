-- Mutable provider-agnostic driver roster (contact + unit assignment cache).
-- Safe to re-run. Applied via create_all in local/dev; use this for existing DBs.
-- No provider-specific columns — filters use canonical fields only.

CREATE TABLE IF NOT EXISTS driver_roster (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(32) NOT NULL,
    tenant_id VARCHAR(128) NOT NULL,
    external_driver_id VARCHAR(128) NOT NULL,
    first_name VARCHAR(256) NULL,
    last_name VARCHAR(256) NULL,
    display_name VARCHAR(512) NULL,
    phone_e164 VARCHAR(32) NULL,
    current_device_id VARCHAR(128) NULL,
    unit_label VARCHAR(256) NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    profile_complete BOOLEAN NOT NULL DEFAULT FALSE,
    has_unit_assignment BOOLEAN NOT NULL DEFAULT FALSE,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_driver_roster_provider_tenant_external
        UNIQUE (provider, tenant_id, external_driver_id)
);

CREATE INDEX IF NOT EXISTS ix_driver_roster_tenant_external
    ON driver_roster (tenant_id, external_driver_id);

CREATE INDEX IF NOT EXISTS ix_driver_roster_tenant_active
    ON driver_roster (tenant_id, is_active);
