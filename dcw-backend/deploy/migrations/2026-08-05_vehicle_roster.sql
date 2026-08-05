-- Mutable provider-agnostic vehicle/unit roster cache.
-- Safe to re-run. Applied via create_all in local/dev; use this for existing DBs.

CREATE TABLE IF NOT EXISTS vehicle_roster (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(32) NOT NULL,
    tenant_id VARCHAR(128) NOT NULL,
    external_device_id VARCHAR(128) NOT NULL,
    name VARCHAR(512) NULL,
    vin VARCHAR(64) NULL,
    current_driver_id VARCHAR(128) NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vehicle_roster_provider_tenant_external
        UNIQUE (provider, tenant_id, external_device_id)
);

CREATE INDEX IF NOT EXISTS ix_vehicle_roster_tenant_external
    ON vehicle_roster (tenant_id, external_device_id);
