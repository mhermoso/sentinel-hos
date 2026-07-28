# 8. Documentation

Documentation that enables engineers, integrators, and customers to build, operate, and trust Sentinel HOS without tribal knowledge.

**Depends on:** All workstreams (document as you build)  
**Required before:** [10 — Launch Readiness](./10-launch-readiness.md)

---

## Things to Consider

### Audiences

| Audience | Needs | Format |
|----------|-------|--------|
| Internal engineers | Architecture, setup, conventions | Repo docs, ADRs |
| External integrators | API reference, auth, webhooks | Published docs site |
| Fleet operators | Compliance concepts, setup, FAQ | User guide |
| Compliance/legal | Data handling, audit capabilities | Security whitepaper |
| On-call / SRE | Deploy, rollback, incidents | Runbooks |

### Documentation principles

- **Docs as code** — Markdown in repo; reviewed in PRs alongside code changes.
- **Single source of truth** — OpenAPI spec generates API docs; don't duplicate endpoint lists manually.
- **Versioned docs** — Docs site version matches API version (`/v1/` docs for v1 API).
- **Executable examples** — curl commands and SDK snippets that actually work against sandbox.
- **Update on change** — PR template checkbox: "Docs updated if user-facing behavior changed."

### ADR (Architecture Decision Record) format

Each ADR should capture:
- **Status** — Proposed, accepted, deprecated, superseded
- **Context** — What problem forced a decision
- **Decision** — What was chosen
- **Consequences** — Tradeoffs, what becomes easier/harder
- **Alternatives considered** — What was rejected and why

---

## Tasks to Complete

### Repository documentation

- [ ] Expand `README.md`: quick start, architecture overview, links to docs
- [ ] Add `CONTRIBUTING.md`: branch strategy, PR process, code style, test requirements
- [ ] Add `CODE_OF_CONDUCT.md` (if open source or external contributors expected)
- [ ] Add `CHANGELOG.md` with Keep a Changelog format
- [ ] Add `docs/adr/` directory with ADR template (`0000-template.md`)
- [ ] Maintain index of all ADRs in `docs/adr/README.md`

### Developer setup guide

- [ ] Prerequisites: language version, Docker, cloud CLI, etc.
- [ ] Clone, install dependencies, configure `.env` from `.env.example`
- [ ] Start local dependencies (Postgres, queue) via Docker Compose
- [ ] Run migrations and seed data
- [ ] Run test suite locally
- [ ] Run API locally and hit health endpoint
- [ ] Troubleshooting section: common errors and fixes
- [ ] Verify guide on fresh machine (new engineer onboarding test)

### Architecture documentation

- [ ] System context diagram (C4 level 1): Sentinel HOS and external systems
- [ ] Container diagram (C4 level 2): services and data stores
- [ ] Data flow diagram: ingest → canonical → evaluate → API
- [ ] Sequence diagram: webhook ingest to violation alert
- [ ] Document evaluation model: triggers, caching, replay
- [ ] Document multi-tenancy model

### Domain & rule documentation

- [ ] Glossary: duty status, violation, rule pack, canonical event, etc.
- [ ] Domain model reference (entities and relationships)
- [ ] FMCSA rule pack reference: each rule, CFR citation, examples, edge cases
- [ ] Document known limitations and out-of-scope rules for v1
- [ ] Document timezone and 24-hour period policy in plain language
- [ ] FAQ for common compliance questions ("Does a 30-min break pause the 14-hour clock?")

### API documentation

- [ ] Publish OpenAPI spec (Redoc, Swagger UI, or docs site)
- [ ] Authentication guide: API keys, scopes, rotation
- [ ] Quickstart: first API call in 5 minutes
- [ ] Endpoint reference generated from OpenAPI (not hand-maintained)
- [ ] Webhook documentation: events, payloads, signature verification, retries
- [ ] Error codes reference
- [ ] Rate limits and pagination guide
- [ ] Changelog for API breaking changes

### Provider integration guides

- [ ] Per-provider setup guide: credentials, permissions, webhook URL config
- [ ] Provider-specific field mapping notes
- [ ] Known provider limitations and workarounds
- [ ] Sync frequency and latency expectations
- [ ] Troubleshooting: sync failures, missing drivers, auth errors

### Operator / runbooks

- [ ] Deploy runbook: staging and production promote steps
- [ ] Rollback runbook: revert to previous version
- [ ] Database migration runbook: apply, verify, rollback
- [ ] Incident response runbook (link to [05 — Security](./05-security-privacy-and-compliance.md))
- [ ] Provider outage runbook: disable sync, communicate to customers
- [ ] Rule pack upgrade runbook: pin, test, rollout, replay
- [ ] On-call handbook: alert catalog, escalation, useful queries/dashboards

### Customer-facing documentation

- [ ] Getting started guide for fleet operators
- [ ] Connecting your ELD provider (step-by-step with screenshots)
- [ ] Understanding compliance dashboard / API responses
- [ ] Exporting violation reports for audits
- [ ] Data retention and privacy overview (customer-facing summary)

### Security & compliance docs

- [ ] Security whitepaper or trust page: encryption, tenant isolation, audit logs
- [ ] Subprocessor list
- [ ] Data processing overview for customer security questionnaires
- [ ] SOC 2 status page (in progress / certified)

### Documentation site

- [ ] Choose docs platform: Docusaurus, MkDocs, GitBook, ReadMe.com
- [ ] Set up docs site with version selector
- [ ] CI: deploy docs on merge to main
- [ ] Custom domain (docs.sentinel-hos.com or similar)
- [ ] Search enabled

### Documentation quality

- [ ] Define doc review checklist for PRs
- [ ] Schedule quarterly doc audit: broken links, outdated screenshots, stale ADRs
- [ ] Collect feedback mechanism on docs pages ("Was this helpful?")

---

## Success Criteria

- New engineer productive locally within 4 hours using setup guide only
- Integrator can connect and query compliance without calling engineering
- Every v1 FMCSA rule has a documented example in rule pack docs
- Runbooks exist for deploy, rollback, and top 3 incident types
- Docs site live and linked from README before beta

---

## Open Questions

1. Public docs site at alpha, or private until beta?
2. Host docs in-repo (MkDocs) vs managed platform (ReadMe, GitBook)?
3. Video walkthroughs for fleet operators, or text-only for v1?

---

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
