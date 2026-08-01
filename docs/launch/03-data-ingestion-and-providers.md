# 3. Data Ingestion & Multi-Provider Support

Normalize fragmented ELD and telematics data into the canonical HOS model so the rule engine evaluates one authoritative timeline per driver.

**Depends on:** [01 — Foundation & Decisions](./01-foundation-and-decisions.md)  
**Blocks:** [02 — Core Rule Engine](./02-core-rule-engine.md) (needs real data), [04 — API & Platform](./04-api-and-platform.md)

---

## Things to Consider

### Why multi-provider is hard

- **Different event granularities** — Some ELDs emit second-level status changes; others batch every 5 minutes.
- **Different status vocabularies** — "OFF", "Off Duty", "off_duty", "OD" — all must map to canonical enum.
- **Different identifiers** — Provider driver ID ≠ internal driver ID; mapping tables and merge logic required.
- **Certified vs non-certified data** — FMCSA-certified ELD output has specific fields; telematics-only sources may lack required annotations.
- **Edit and annotation models** — Providers represent log edits, certifications, and exceptions differently.

### Ingestion patterns

| Pattern | Use case | Latency | Complexity |
|---------|----------|---------|------------|
| Webhooks | Provider pushes events on change | Seconds | Medium |
| Polling API | Scheduled pull from provider REST API | Minutes | Low |
| Streaming (Kafka/SSE) | High-volume real-time fleets | Sub-second | High |
| Batch file import | Migration, backfill, CSV/JSON dumps | Hours | Low |
| SFTP / scheduled drops | Legacy fleet systems | Hours | Medium |

- **v1 recommendation:** Start with polling + webhooks for one provider; add batch import for backfill.

### Canonical schema design

- **Immutable events** — Prefer append-only; corrections arrive as new events or explicit edit records, not overwrites.
- **Provenance** — Every canonical event stores: `source_provider`, `source_event_id`, `ingested_at`, `raw_payload_ref`.
- **Idempotency** — Same provider event ingested twice must not duplicate canonical events (dedupe key: provider + source_event_id).
- **Ordering** — Events ordered by `event_time` (not ingest time); handle out-of-order delivery.

### Provider priority (candidates — confirm with design partners)

| Provider | API maturity | Market share | Notes |
|----------|--------------|--------------|-------|
| Samsara | Strong REST + webhooks | Large | Good first target |
| Geotab | MyGeotab API, widely used | Very large | Complex data model |
| Motive (KeepTruckin) | REST API | Large | |
| Omnitracs | Enterprise | Medium | Often larger fleets |
| Platform Science | Growing | Medium | |
| Verizon Connect | REST | Medium | |

- **Decision:** Pick ONE provider for v1 end-to-end; stub adapter interface for second.

### Data quality dimensions

- **Completeness** — Gaps in timeline, missing co-driver, missing vehicle
- **Consistency** — Overlapping statuses, driving without vehicle assignment
- **Timeliness** — Events arriving hours late affect real-time compliance
- **Accuracy** — GPS vs manual status mismatch (flag, don't silently fix)

### Conflict reconciliation

- **Single provider of record per driver** — Simplest: one active ELD source; others are supplementary.
- **Multi-source merge** — Harder: weighted trust, latest-wins, or manual override. Defer to post-v1 unless required.
- **Duplicate detection** — Same timestamp + status from re-ingest vs genuine duplicate event.

---

## Tasks to Complete

### Canonical schema

- [ ] Finalize `CanonicalLogEvent` JSON Schema / protobuf definition
- [ ] Finalize `Driver`, `Vehicle`, `Organization` schemas
- [ ] Define status enum and mapping table structure
- [ ] Define edit/certification record schema (if separate from events)
- [ ] Publish schema version policy (semver, backward compatibility rules)
- [ ] Generate validation code from schema (e.g. JSON Schema → validators)

### Ingestion pipeline architecture

- [ ] Design pipeline stages: receive → validate → transform → dedupe → store → trigger evaluation
- [ ] Implement dead-letter queue for unprocessable events
- [ ] Implement ingest audit log: raw payload stored (or S3 ref), transform result, errors
- [ ] Define backpressure handling when provider sends burst traffic
- [ ] Implement out-of-order event handler (late events trigger re-evaluation)

### Provider adapter framework

- [ ] Define `ProviderAdapter` interface:
  - `fetch_events(driver, since) → []ProviderEvent`
  - `handle_webhook(payload) → []ProviderEvent`
  - `map_to_canonical(provider_event) → CanonicalLogEvent`
  - `validate_credentials() → bool`
- [ ] Define adapter registration and config per organization
- [ ] Implement adapter health check and last-sync timestamp
- [ ] Implement credential storage integration (secrets manager, not DB plaintext)

### Provider adapter #1 (v1)

- [ ] Select provider based on design partner
- [ ] Obtain sandbox / dev API credentials
- [ ] Map provider driver list → internal Driver entities
- [ ] Map provider vehicle list → internal Vehicle entities
- [ ] Implement status code mapping table
- [ ] Implement event fetch (polling) with pagination and cursor
- [ ] Implement webhook receiver (if provider supports)
- [ ] Handle provider rate limits and exponential backoff
- [ ] Write adapter integration tests against sandbox API
- [ ] Document provider-specific quirks and limitations

### Provider adapter #2 (stub or full)

- [ ] Implement second adapter OR create `MockProviderAdapter` for tests/demo
- [ ] Verify adapter interface covers both without provider-specific leaks

### Batch import

- [ ] Define CSV/JSON import format for manual migration
- [ ] Implement import CLI: `sentinel import --file logs.json --provider manual`
- [ ] Validate import file against canonical schema before ingest
- [ ] Support dry-run mode with validation report

### Data quality

- [ ] Implement gap detection job (missing status segments > N minutes)
- [ ] Implement overlap detection job
- [ ] Implement stale data alert (no events for active driver in X hours)
- [ ] Implement impossible timeline detector (driving speed implied > threshold)
- [ ] Surface data quality issues in API (don't hide from compliance consumers)
- [ ] Define policy: evaluate with gaps vs block evaluation (document per org config)

### Conflict & deduplication

- [ ] Implement dedupe key strategy per provider
- [ ] Implement canonical event upsert policy (append-only vs versioned update)
- [ ] Document single-source-of-truth policy for v1
- [ ] Plan multi-source reconciliation design (even if deferred)

### Sync orchestration

- [ ] Implement scheduled sync job per organization (cron / queue worker)
- [ ] Implement per-driver sync on webhook trigger
- [ ] Track sync state: last_successful_sync, cursor, error_count
- [ ] Alert on repeated sync failures
- [ ] Support manual "force resync" for driver date range (admin operation)

---

## Success Criteria

- End-to-end: provider sandbox event → canonical store → rule engine evaluation → correct violation
- Duplicate ingest does not create duplicate canonical events
- Adapter documentation allows adding a new provider without changing engine code
- Data quality issues visible in API within 5 minutes of detection

---

## Open Questions

1. Which provider is the v1 adapter target?
2. Store full raw provider payloads, or only canonical events + minimal provenance?
3. Real-time webhooks required for v1, or is 5-minute polling acceptable?
4. Support manual log entry / CSV import for fleets without API access at launch?

---

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
