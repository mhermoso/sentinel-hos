-- Phase 3: per-driver ruleset configuration (mutable onboarding table).
-- Safe to re-run. Applied via create_all in local/dev; use this for existing DBs.

CREATE TABLE IF NOT EXISTS driver_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    driver_id VARCHAR(128) NOT NULL,
    operating_authority VARCHAR(32) NOT NULL DEFAULT 'INTERSTATE',
    short_haul_eligible BOOLEAN NOT NULL DEFAULT false,
    cdl_required BOOLEAN NOT NULL DEFAULT true,
    cycle VARCHAR(16) NOT NULL DEFAULT '70_8',
    home_terminal_timezone VARCHAR(64) NOT NULL,
    work_reporting_lat DOUBLE PRECISION NULL,
    work_reporting_lon DOUBLE PRECISION NULL,
    vehicle_weight_class VARCHAR(64) NULL,
    hazmat_placard BOOLEAN NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_driver_profiles_tenant_driver UNIQUE (tenant_id, driver_id)
);

CREATE INDEX IF NOT EXISTS ix_driver_profiles_tenant_driver
    ON driver_profiles (tenant_id, driver_id);
