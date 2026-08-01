# Sentinel HOS — Launch Checklist

Action items to take Sentinel HOS from early-stage concept to a production-ready compliance platform.

**Current state:** README only. Architecture, provider adapters, and regulatory rule packs are not yet implemented.

Each section has a **detailed planning document** with considerations, tasks, success criteria, and open questions:

| # | Area | Document |
|---|------|----------|
| 1 | Foundation & Decisions | [docs/launch/01-foundation-and-decisions.md](./docs/launch/01-foundation-and-decisions.md) |
| 2 | Core Rule Engine | [docs/launch/02-core-rule-engine.md](./docs/launch/02-core-rule-engine.md) |
| 3 | Data Ingestion & Providers | [docs/launch/03-data-ingestion-and-providers.md](./docs/launch/03-data-ingestion-and-providers.md) |
| 4 | API & Platform | [docs/launch/04-api-and-platform.md](./docs/launch/04-api-and-platform.md) |
| 5 | Security, Privacy & Compliance | [docs/launch/05-security-privacy-and-compliance.md](./docs/launch/05-security-privacy-and-compliance.md) |
| 6 | Testing & Quality | [docs/launch/06-testing-and-quality.md](./docs/launch/06-testing-and-quality.md) |
| 7 | Infrastructure & DevOps | [docs/launch/07-infrastructure-and-devops.md](./docs/launch/07-infrastructure-and-devops.md) |
| 8 | Documentation | [docs/launch/08-documentation.md](./docs/launch/08-documentation.md) |
| 9 | Legal & Business | [docs/launch/09-legal-and-business.md](./docs/launch/09-legal-and-business.md) |
| 10 | Launch Readiness | [docs/launch/10-launch-readiness.md](./docs/launch/10-launch-readiness.md) |

Full index: [docs/launch/README.md](./docs/launch/README.md)

---

## Quick summary

### 1. Foundation & Decisions
Tech stack, architecture, domain model, FMCSA scope, determinism guarantees, repo scaffold.

### 2. Core Rule Engine
Rule pack framework, FMCSA v1 rules, violations, rest/break logic, audit trail, golden tests.

### 3. Data Ingestion & Multi-Provider Support
Canonical schema, provider adapters, reconciliation, real-time vs batch, data quality.

### 4. API & Platform
REST API, auth/RBAC, multi-tenancy, webhooks, optional admin UI.

### 5. Security, Privacy & Compliance (Product)
Encryption, access control, retention, SOC 2 roadmap, incident response.

### 6. Testing & Quality
Unit/integration/golden tests, regulatory fixtures, load and chaos testing, UAT.

### 7. Infrastructure & DevOps
Environments, CI/CD, observability, migrations, secrets, backups, rate limiting.

### 8. Documentation
Developer setup, ADRs, API docs, rule pack docs, provider guides, runbooks.

### 9. Legal & Business
License, ToS, privacy policy, DPA, liability, pricing.

### 10. Launch Readiness
Alpha/beta/GA criteria, support channels, monitoring, post-launch plan.

---

## Suggested Phase Order

| Phase | Focus | Goal |
|-------|--------|------|
| **0** | [01 — Foundation](./docs/launch/01-foundation-and-decisions.md) | Runnable skeleton, ADRs, CI |
| **1** | [02 — Rule Engine](./docs/launch/02-core-rule-engine.md) | Deterministic compliance on canonical data |
| **2** | [03 — Ingestion](./docs/launch/03-data-ingestion-and-providers.md) + [04 — API](./docs/launch/04-api-and-platform.md) | End-to-end ingest → evaluate → query |
| **3** | [05 — Security](./docs/launch/05-security-privacy-and-compliance.md) + [06 — Testing](./docs/launch/06-testing-and-quality.md) + [07 — Infra](./docs/launch/07-infrastructure-and-devops.md) | Production-grade hardening |
| **4** | [10 — Launch Readiness](./docs/launch/10-launch-readiness.md) (alpha/beta) | Validate accuracy and UX |
| **5** | [08 — Docs](./docs/launch/08-documentation.md) + [09 — Legal](./docs/launch/09-legal-and-business.md) + GA | General availability |

---

## Open Questions (resolve before build)

See [01 — Foundation & Decisions](./docs/launch/01-foundation-and-decisions.md#open-questions) for the full list. Top priorities:

1. **Deployment model** — SaaS only, self-hosted, or both?
2. **First target customer** — Fleet size, industry, primary ELD vendor?
3. **Real-time requirement** — Sub-second alerts vs minute-level batch acceptable for v1?
4. **Jurisdictions beyond FMCSA** — Required at launch or post-v1?
5. **Build vs buy** — Existing open-source HOS libraries to evaluate or extend?

---

*Last updated: 2026-07-28*
