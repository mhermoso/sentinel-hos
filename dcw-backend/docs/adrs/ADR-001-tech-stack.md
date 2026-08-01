# ADR-001: Technology Stack Selection

## Status

Accepted

## Context

Driver Compliance Watch (DCW) requires high performance, strict computational determinism, low latency, and robust type safety to evaluate 49 CFR Part 395 FMCSA Hours of Service (HOS) regulations.

## Decision

We select the following technology stack:

* **Language**: Python 3.12+ (leveraging performance improvements, native type hinting, and `zoneinfo`).
* **Web & Engine Framework**: FastAPI (high-speed async REST endpoints) + Pydantic v2 (Rust-backed payload validation).
* **Primary Database**: PostgreSQL 16 (ACID relational audit compliance + `JSONB` raw telematics retention).
* **In-Memory Cache & Queue**: Redis 7.2 (driver status caching, cursor retention, pub/sub alert routing).
* **Async Background Processing**: Python ARQ (Redis-backed job queue for polling and background sweeps).
* **Deployment & Security**: Podman rootless containers (`podman kube play`).

## Consequences

* Single primary language across ingestion, engine, and dashboard domains accelerates development.
* Pydantic v2 provides sub-millisecond serialization speeds required for real-time compliance evaluation.
