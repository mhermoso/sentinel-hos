# ADR-005: Three-Tier Timezone and Datetime Handling Policy

## Status

Accepted

## Context

Commercial trucks cross multiple state lines and time zones during a single shift. Daylight Saving Time (DST) changes can create phantom violations if timestamps are not strictly handled.

## Decision

We enforce a **Three-Tier Timezone Model**:

1. **UTC Storage**: All database timestamps and interval math are strictly stored/calculated in UTC.
2. **Home Terminal Timezone**: Daily 24-hour log sheet boundaries are evaluated against the driver's Home Terminal IANA timezone (49 CFR § 395.8).
3. **Event Location Timezone**: Derived from GPS coordinates for display context on dashboards and reports.

## Consequences

* Absolute immunity from DST jump discrepancies.
* Strict compliance with FMCSA daily grid graph boundaries.
