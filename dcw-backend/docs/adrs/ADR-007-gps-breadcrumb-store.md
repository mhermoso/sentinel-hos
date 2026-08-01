# ADR-007: GPS Breadcrumb Store (Append-Only, Separate from HOS)

## Status

Accepted

## Context

Driver-day route maps need dense GPS trails. Geotab `DutyStatusLog` events carry sparse lat/lon at status changes only. Dense position history comes from Geotab `LogRecord` (device GPS breadcrumbs). Mixing breadcrumbs into `canonical_hos_logs` would violate ADR-003 (duty-status canonical events) and risk polluting the compliance engine input stream.

## Decision

1. **Separate append-only table** `gps_breadcrumbs` stores Geotab LogRecord (and future provider) GPS points. This store is **not** part of the HOS canonical event model (ADR-003 remains duty-status only).
2. **Engine never reads** `gps_breadcrumbs`. Compliance evaluation continues to use only `canonical_hos_logs` / audit inputs.
3. **Normalization**: GPS coordinates are rounded to 4 decimal places (~11.1 m), matching the HOS normalizer. Timestamps are truncated to 1-second precision. Each row carries an `inputs_hash` (SHA-256) for integrity.
4. **Driver attribution at ingest**: LogRecords are device-scoped. At persist time, `driver_id` is resolved from the latest `canonical_hos_logs` row for that device with `event_timestamp <= breadcrumb_ts`. If none exists, use `unassigned:device:{device_id}`.
5. **Immutability**: SQL `UPDATE` and `DELETE` on `gps_breadcrumbs` are blocked by a PostgreSQL trigger (same pattern as `canonical_hos_logs`). Dedup is `(tenant_id, raw_id)`.
6. **Privacy**: Location is stored to support compliance map views (route trails, alert placement). Retention and redaction policy are **TBD** and out of scope for this ADR; document intent only.

## Consequences

* Route map APIs can join dense GPS with HOS status timelines without changing the engine contract.
* HOS event store remains a pure duty-status ledger for audits and rule evaluation.
* Historical backfill (Get `LogRecord` by date range) and live polling (GetFeed) share the same schema.
