# Launch Documentation Index

Detailed planning documents for taking Sentinel HOS to production.

| # | Document | Summary |
|---|----------|---------|
| 1 | [Foundation & Decisions](./01-foundation-and-decisions.md) | Tech stack, architecture, domain model, regulatory scope |
| 2 | [Core Rule Engine](./02-core-rule-engine.md) | FMCSA rules, violations, audit trail, determinism |
| 3 | [Data Ingestion & Providers](./03-data-ingestion-and-providers.md) | Canonical schema, ELD adapters, data quality |
| 4 | [API & Platform](./04-api-and-platform.md) | REST API, auth, multi-tenancy, webhooks, UI |
| 5 | [Security, Privacy & Compliance](./05-security-privacy-and-compliance.md) | Encryption, access control, SOC 2, incident response |
| 6 | [Testing & Quality](./06-testing-and-quality.md) | Golden tests, integration, load, UAT |
| 7 | [Infrastructure & DevOps](./07-infrastructure-and-devops.md) | CI/CD, observability, backups, environments |
| 8 | [Documentation](./08-documentation.md) | Dev guides, ADRs, API docs, runbooks |
| 9 | [Legal & Business](./09-legal-and-business.md) | License, ToS, DPA, pricing, liability |
| 10 | [Launch Readiness](./10-launch-readiness.md) | Alpha/beta/GA criteria, support, rollout |

## Suggested reading order

1. Start with **01 Foundation** — decisions here unblock everything else.
2. Build **02 Rule Engine** and **03 Ingestion** in parallel once schema is defined.
3. Expose via **04 API** when engine + one adapter work end-to-end.
4. Harden with **05 Security**, **06 Testing**, and **07 Infrastructure** before external users.
5. Document continuously per **08 Documentation**.
6. Resolve **09 Legal** before beta customers sign up.
7. Execute **10 Launch Readiness** when approaching alpha/beta/GA milestones.

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
