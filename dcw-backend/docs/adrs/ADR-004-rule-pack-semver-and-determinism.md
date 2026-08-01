# ADR-004: Rule Pack Versioning and Determinism Guarantees

## Status

Accepted

## Context

Regulatory laws change over time. Calculations must produce identical outputs when re-evaluating historical log data against the regulatory rules active at that exact point in time.

## Decision

Compliance rule sets are packaged as versioned **Rule Packs** following Semantic Versioning (e.g., `fmcsa-us-property@1.2.0`). Every generated audit record binds the inputs hash to the specific rule pack version executed.

## Consequences

* Guaranteed mathematical determinism across historical replays and legal audits.
