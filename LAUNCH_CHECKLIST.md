# Sentinel HOS — Launch Checklist

Action items to take Sentinel HOS from early-stage concept to a production-ready compliance platform.

**Current state (2026-07-30):** Runnable modular monolith in `dcw-backend/` with ADRs 001–006, Geotab ingestion, FMCSA rule pack `fmcsa-us-property@1.3.0`, compliance sweeper, Twilio notifier (dry-run supported), and HTMX HOS timeline UI. Motive/Samsara adapters, golden fixture suite, PDF audits, and production auth/ops hardening remain open.

Each section has a **detailed planning document** with considerations, tasks, success criteria, and open questions:

| # | Area | Document | Progress |
|---|------|----------|----------|
| 1 | Foundation & Decisions | [docs/launch/01-foundation-and-decisions.md](./docs/launch/01-foundation-and-decisions.md) | Largely done (stack, ADRs, scaffold) |
| 2 | Core Rule Engine | [docs/launch/02-core-rule-engine.md](./docs/launch/02-core-rule-engine.md) | In progress (v1 rules + unit tests; golden suite TBD) |
| 3 | Data Ingestion & Providers | [docs/launch/03-data-ingestion-and-providers.md](./docs/launch/03-data-ingestion-and-providers.md) | Partial (Geotab live; Motive/Samsara stubs; ZOH TBD) |
| 4 | API & Platform | [docs/launch/04-api-and-platform.md](./docs/launch/04-api-and-platform.md) | Partial (REST + HTMX UI; auth/RBAC TBD) |
| 5 | Security, Privacy & Compliance | [docs/launch/05-security-privacy-and-compliance.md](./docs/launch/05-security-privacy-and-compliance.md) | Early |
| 6 | Testing & Quality | [docs/launch/06-testing-and-quality.md](./docs/launch/06-testing-and-quality.md) | Partial (unit tests; golden/integration TBD) |
| 7 | Infrastructure & DevOps | [docs/launch/07-infrastructure-and-devops.md](./docs/launch/07-infrastructure-and-devops.md) | Partial (Compose/Kube deploy files; prod ops TBD) |
| 8 | Documentation | [docs/launch/08-documentation.md](./docs/launch/08-documentation.md) | Partial (ADRs + backend README) |
| 9 | Legal & Business | [docs/launch/09-legal-and-business.md](./docs/launch/09-legal-and-business.md) | Partial (FSL license + ToS draft in `dcw-backend/`) |
| 10 | Launch Readiness | [docs/launch/10-launch-readiness.md](./docs/launch/10-launch-readiness.md) | Not started (pre-alpha) |

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

| Phase | Focus | Goal | Status |
|-------|--------|------|--------|
| **0** | [01 — Foundation](./docs/launch/01-foundation-and-decisions.md) | Runnable skeleton, ADRs, CI | Done / maintaining |
| **1** | [02 — Rule Engine](./docs/launch/02-core-rule-engine.md) | Deterministic compliance on canonical data | In progress |
| **2** | [03 — Ingestion](./docs/launch/03-data-ingestion-and-providers.md) + [04 — API](./docs/launch/04-api-and-platform.md) | End-to-end ingest → evaluate → query | In progress (Geotab path) |
| **3** | [05 — Security](./docs/launch/05-security-privacy-and-compliance.md) + [06 — Testing](./docs/launch/06-testing-and-quality.md) + [07 — Infra](./docs/launch/07-infrastructure-and-devops.md) | Production-grade hardening | Next |
| **4** | [10 — Launch Readiness](./docs/launch/10-launch-readiness.md) (alpha/beta) | Validate accuracy and UX | Later |
| **5** | [08 — Docs](./docs/launch/08-documentation.md) + [09 — Legal](./docs/launch/09-legal-and-business.md) + GA | General availability | Later |

---

## Near-term gaps (highest leverage)

1. **Golden fixture suite** for rule pack contracts (`tests/fixtures/golden/`)
2. **ZOH forward-fill** in the normalizer (specified, not implemented)
3. **Motive / Samsara** adapters beyond stubs
4. **Auth / RBAC / tenant isolation** on API routes
5. **PDF executive audits** (WeasyPrint dependency present; generation not wired)
6. Design-partner validation vs ELD-native reports

---

## Open Questions (resolve before external alpha)

See [01 — Foundation & Decisions](./docs/launch/01-foundation-and-decisions.md#open-questions) for the full list. Top remaining priorities:

1. **First target customer** — Fleet size, industry, primary ELD vendor?
2. **Design partner** — Who provides anonymized or live validation data?
3. **Jurisdictions beyond FMCSA** — Required at launch or post-v1?
4. **Production auth model** — API keys vs OIDC; multi-tenant isolation strategy for SaaS

---

*Last updated: 2026-07-30*
