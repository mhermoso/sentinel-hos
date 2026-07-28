# 4. API & Platform

Expose compliance state and violations to dispatchers, safety teams, integrations, and optional admin UI.

**Depends on:** [01 — Foundation](./01-foundation-and-decisions.md), [02 — Rule Engine](./02-core-rule-engine.md), [03 — Ingestion](./03-data-ingestion-and-providers.md)  
**Blocks:** [10 — Launch Readiness](./10-launch-readiness.md)

---

## Things to Consider

### API design principles

- **Read-heavy workload** — Most calls query compliance snapshots; writes are mostly ingest (often internal, not public).
- **Version from day one** — `/v1/` prefix; breaking changes require new version, not silent changes.
- **Idempotent writes** — Ingest and webhook endpoints must handle retries safely.
- **Pagination everywhere** — Violation history and event logs can be large; cursor-based pagination preferred.
- **Explicit freshness** — Every compliance response includes `evaluated_at`, `data_through`, and `rule_pack_version`.

### Consumer personas

| Persona | Primary needs | Access pattern |
|---------|---------------|----------------|
| Dispatcher | Drive time remaining, can assign load? | Real-time, per driver |
| Safety manager | Violations, trends, audit export | Batch reports, dashboards |
| Compliance officer | Historical violations, dispute evidence | Date range queries, exports |
| Integration (TMS/dispatch) | Webhooks on violation/warning | Event-driven |
| Admin | Org config, provider credentials, users | Infrequent CRUD |

### Auth models

- **API keys** — Simplest for server-to-server integrations; scoped per org.
- **OAuth 2.0 / OIDC** — For user-facing apps and SSO (Google, Okta, Azure AD).
- **JWT with short TTL** — For session-based UI; refresh token rotation.
- **Service accounts** — Internal workers (sync jobs) use separate credentials from customer keys.

### Multi-tenancy

- **Tenant = Organization (fleet operator)** — All data scoped by `org_id`; never leak cross-tenant.
- **Row-level security** — Enforce at DB layer, not only API layer.
- **Per-tenant config** — Rule pack version, 60 vs 70-hour cycle, warning thresholds, active providers.
- **Quotas** — API rate limits, driver count limits per plan tier.

### Real-time vs polling

- **Webhooks** — Push `violation.created`, `warning.approaching_limit`, `driver.status_changed` to customer URLs.
- **SSE or WebSocket** — Optional for live dashboard; higher ops cost.
- **Polling** — Acceptable for v1 if webhooks cover integrations; document recommended poll intervals.

### Admin UI scope for v1

- **Minimum viable UI:** Driver list with compliance status, violation detail, data quality flags.
- **Defer:** Complex rule pack editor, custom report builder, mobile app.
- **Alternative:** API-only v1 with Retool/Metabase internal dashboard for design partners.

---

## Tasks to Complete

### API specification

- [ ] Choose REST (recommended v1) vs GraphQL; document rationale in ADR
- [ ] Write OpenAPI 3.1 spec for all v1 endpoints
- [ ] Define error response format (RFC 7807 Problem Details)
- [ ] Define pagination contract (cursor, limit, has_more)
- [ ] Define filtering/sorting conventions (`?status=active&sort=-evaluated_at`)
- [ ] Set up API spec linting in CI (Spectral, Redocly)

### Core read endpoints

- [ ] `GET /v1/drivers` — List drivers with current compliance summary
- [ ] `GET /v1/drivers/{id}` — Driver profile + current compliance snapshot
- [ ] `GET /v1/drivers/{id}/compliance` — Detailed compliance: limits remaining, active warnings
- [ ] `GET /v1/drivers/{id}/violations` — Violation history (paginated, filter by date/type)
- [ ] `GET /v1/drivers/{id}/events` — Canonical log events (paginated, for audit/debug)
- [ ] `GET /v1/drivers/{id}/project` — Project compliance if driver continues activity (POST with hypothetical)
- [ ] `GET /v1/fleets/{id}/summary` — Fleet-wide violation counts, drivers at risk
- [ ] `GET /v1/evaluations/{id}` — Fetch stored evaluation record (audit/dispute)

### Write / admin endpoints

- [ ] `POST /v1/organizations` — Create org (internal/admin only at first)
- [ ] `PATCH /v1/organizations/{id}/settings` — Cycle type, warning thresholds, rule pack pin
- [ ] `POST /v1/providers/{provider}/connect` — Store provider credentials, start sync
- [ ] `GET /v1/providers/{provider}/status` — Sync health, last sync time
- [ ] `POST /v1/drivers/{id}/resync` — Trigger manual backfill for date range
- [ ] `POST /v1/webhooks` — Register customer webhook endpoints
- [ ] `DELETE /v1/webhooks/{id}` — Remove webhook subscription

### Authentication & authorization

- [ ] Implement API key issuance and revocation per org
- [ ] Implement OAuth/OIDC flow (or defer with API-key-only v1 — document decision)
- [ ] Define roles: `admin`, `safety`, `dispatcher`, `read_only`
- [ ] Implement RBAC middleware on all endpoints
- [ ] Enforce org scoping on every DB query (defense in depth)
- [ ] Implement audit log for admin actions (credential changes, settings updates)

### Webhooks (outbound)

- [ ] Define webhook event types and payload schemas
- [ ] Implement webhook delivery with retries (exponential backoff, max attempts)
- [ ] Implement webhook signing (HMAC-SHA256) for customer verification
- [ ] Provide webhook test/delivery log in API or admin UI
- [ ] Document webhook setup guide for integrators

### Rate limiting & quotas

- [ ] Implement per-org rate limits (requests/minute)
- [ ] Return `429` with `Retry-After` header
- [ ] Implement driver-count quota enforcement per plan
- [ ] Expose rate limit headers (`X-RateLimit-Remaining`)

### Caching strategy

- [ ] Cache compliance snapshots with TTL keyed by `(driver_id, last_event_id)`
- [ ] Invalidate cache on new event ingest for driver
- [ ] Document cache staleness bounds in API responses
- [ ] Avoid caching violation history (audit accuracy)

### Admin UI (if in v1 scope)

- [ ] Choose stack (React + internal design system, or Retool for speed)
- [ ] Driver list page: status badge, drive time remaining, last sync
- [ ] Driver detail page: timeline, violations, data quality flags
- [ ] Org settings page: provider connection, cycle type, webhooks
- [ ] Violation export (CSV/PDF) for compliance officers
- [ ] Role-based UI visibility matching API RBAC

### SDK & integrator experience

- [ ] Generate API client from OpenAPI (TypeScript, Python minimum)
- [ ] Publish quickstart guide: auth, first driver query, webhook setup
- [ ] Provide Postman/Insomnia collection
- [ ] Sandbox environment with fake driver data

---

## Success Criteria

- OpenAPI spec matches implemented endpoints; CI validates spec on every PR
- Design partner can query driver compliance and receive violation webhooks without engineering support
- No cross-tenant data access in penetration test or automated security tests
- p95 read latency < 200ms for single-driver compliance snapshot (excluding cold cache)

---

## Open Questions

1. API-key-only for v1, or OAuth required from day one?
2. Build custom admin UI vs use Retool/internal tools for alpha?
3. Public sandbox API for prospects, or invite-only?
4. GraphQL on roadmap for complex fleet queries?

---

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
