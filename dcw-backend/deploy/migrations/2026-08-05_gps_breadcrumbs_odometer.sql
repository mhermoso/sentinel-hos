-- Optional odometer (meters) on GPS breadcrumbs for Samsara OBD/GPS odometer
-- merge. Safe to re-run. create_all adds the column on fresh DBs.

ALTER TABLE gps_breadcrumbs
    ADD COLUMN IF NOT EXISTS odometer_m DOUBLE PRECISION NULL;
