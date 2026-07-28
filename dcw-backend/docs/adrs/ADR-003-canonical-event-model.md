# ADR-003: Canonical Log Event Model & Immutability

## Status

Accepted

## Context

Heterogeneous telematics providers (Geotab, Motive, Samsara) format status logs differently. Downstream compliance calculations require a single, standardized, immutable data representation.

## Decision

We establish the `DCWCanonicalHOSLog` schema as the universal system contract. All provider payloads are mapped to this model upon ingestion. Database storage enforces a **100% Append-Only Policy**; SQL `UPDATE` and `DELETE` operations are strictly forbidden.

## Consequences

* Downstream engine logic is completely decoupled from vendor-specific API structures.
* Full evidentiary non-repudiation during DOT audits.
