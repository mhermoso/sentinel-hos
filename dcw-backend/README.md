# Driver Compliance Watch (DCW) — Managed SaaS Platform

Driver Compliance Watch (DCW) is a deterministic 49 CFR Part 395 Hours of Service (HOS) compliance evaluation engine and real-time fleet monitoring platform.

## Features
- **Deterministic Math Engine**: 0% probabilistic or LLM logic in regulatory evaluations.
- **Multi-Tenant SaaS**: Built with single-tenant isolation and per-vehicle/per-month (PVPM) tier gating.
- **Multi-Provider Telematics**: Ingests feeds from Geotab, Motive, and Samsara.
- **Automated Telephony**: Outbound Twilio Voice IVR and SMS warnings for shift violations.

## Quickstart
```bash
# Spin up local PostgreSQL and Redis containers
make db-up

# Run development server
make dev

# Run ARQ background worker
make worker
```
