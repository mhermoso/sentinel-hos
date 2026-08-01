# ADR-002: Modular Monolith Architecture Style

## Status

Accepted

## Context

Choosing between a distributed microservices architecture and a monolith for v1. Microservices add network complexity, inter-service latency, and deployment overhead.

## Decision

We adopt a **Modular Monolith** architecture pattern. All domains (`ingestion`, `engine`, `notifier`, `dashboard`) reside within a single codebase under `app/domains/`, cleanly isolated by domain boundaries. Shared logic is restricted to `app/core/`.

## Consequences

* In-process function calls execute in <2ms without network overhead.
* Simple single-container deployment model.
* Allows seamless extraction of individual domains into microservices in v2 if specific operational bottlenecks demand scaling.
