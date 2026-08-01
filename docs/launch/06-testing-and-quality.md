# 6. Testing & Quality

Ensure the rule engine is correct, the platform is reliable, and regressions are caught before they reach fleets.

**Depends on:** [02 — Rule Engine](./02-core-rule-engine.md), [03 — Ingestion](./03-data-ingestion-and-providers.md), [04 — API](./04-api-and-platform.md)  
**Parallel with:** All development; gates production launch

---

## Things to Consider

### Why testing matters more here

- **Regulatory accuracy** — A false "compliant" result can expose fleets to fines; a false violation wastes dispatcher time and erodes trust.
- **Determinism** — Non-deterministic behavior makes audits impossible and disputes unresolvable.
- **Time complexity** — HOS logic is date/time-heavy; most bugs live at DST boundaries, midnight resets, and timezone edges.
- **Provider chaos** — Real ELD data is messy; tests must use fixtures derived from production-like samples (anonymized).

### Testing pyramid for Sentinel HOS

```
        ┌─────────────┐
        │  E2E / UAT  │  Few: full ingest → evaluate → API → webhook
        ├─────────────┤
        │ Integration │  Moderate: adapter + DB + API
        ├─────────────┤
        │    Unit     │  Many: each rule, normalizer, time utils
        └─────────────┘
```

- **Golden tests are the contract** — For the rule engine, golden fixtures are more important than code coverage percentage.

### Categories of test data

| Category | Purpose | Source |
|----------|---------|--------|
| Synthetic minimal | Single rule isolation | Hand-written |
| Synthetic edge | DST, midnight, splits | Hand-written |
| Regulatory reference | FMCSA-style scenarios | CFR examples, industry guides |
| Anonymized real | Provider quirks | Design partner data |
| Dispute scenarios | Known contested cases | Safety team / legal |

### Non-functional testing

- **Latency** — Single driver evaluation, fleet-wide batch, API p95/p99
- **Throughput** — Events ingested per second during peak (morning dispatch)
- **Correctness under load** — Results identical at 1x vs 100x load (no race conditions)

### CI quality gates

- Unit + golden tests: **block merge on failure**
- Integration tests: block merge (may allow flaky quarantine with ticket)
- Load tests: nightly or pre-release, not every PR
- Security scans: block merge on critical CVEs in dependencies

---

## Tasks to Complete

### Test infrastructure

- [ ] Choose test framework(s) aligned with tech stack
- [ ] Set up test fixtures directory structure: `tests/fixtures/golden/`, `tests/fixtures/providers/`
- [ ] Implement test clock abstraction (freeze time, simulate DST transitions)
- [ ] Implement test timezone helpers (America/Chicago, America/Los_Angeles, UTC)
- [ ] Configure CI to run unit + integration tests on every PR
- [ ] Configure test coverage reporting (informative, not sole quality metric)
- [ ] Set up test database (ephemeral Postgres in CI)

### Unit tests — time & utilities

- [ ] Test UTC storage and timezone conversion (all US timezones)
- [ ] Test DST spring-forward gap (missing hour)
- [ ] Test DST fall-back overlap (repeated hour — policy documented)
- [ ] Test 24-hour period boundary at driver home terminal midnight
- [ ] Test rolling 7/8-day window calculations
- [ ] Test duration accumulators (driving, on-duty, off-duty)
- [ ] Test event normalizer: sort, gap detection, overlap detection

### Unit tests — rule engine (per rule)

- [ ] 11-hour driving: under limit, at limit, over limit
- [ ] 14-hour window: break does not extend window (standard case)
- [ ] 30-minute break: break at 7h59 driving vs 8h01 driving
- [ ] 60/70-hour: rolling window edge (hour drops off window)
- [ ] 34-hour restart: valid (two 1–5 AM periods), invalid (short restart)
- [ ] Sleeper split 8/2: valid pair pauses 14-hour clock
- [ ] Sleeper split 7/3: valid pair pauses 14-hour clock
- [ ] Invalid sleeper split: does not pause clock
- [ ] 10-hour off-duty: required before next driving period
- [ ] Team driver / co-driver scenarios (if in scope)

### Golden / regulatory fixture suite

- [ ] Create 20+ golden scenarios with expected JSON output
- [ ] Include at least 5 "dispute-style" scenarios with documented reasoning
- [ ] Version golden files alongside rule pack version
- [ ] CI: `golden tests` job fails PR if output diff (with approve-to-update workflow)
- [ ] Document how to add new golden cases (contributor guide)
- [ ] Obtain anonymized real logs from design partner → add 3+ to golden suite

### Integration tests — ingestion

- [ ] Adapter maps provider sandbox event → correct canonical event
- [ ] Dedupe: ingest same event twice → one canonical record
- [ ] Out-of-order: late event triggers re-evaluation
- [ ] Dead letter: malformed payload routed to DLQ, not silent drop
- [ ] Batch import: valid file imports; invalid file rejected with error report

### Integration tests — API

- [ ] Auth: valid key succeeds, invalid key 401, wrong org 403
- [ ] Tenant isolation: org A token cannot read org B driver
- [ ] Pagination: cursor returns consistent pages, no duplicates/skips
- [ ] Compliance endpoint returns correct `evaluated_at`, `rule_pack_version`
- [ ] Webhook fires on violation with valid signature
- [ ] Rate limit returns 429 after threshold

### End-to-end tests

- [ ] E2E: seed provider mock → sync → evaluate → API returns violation
- [ ] E2E: settings change (60→70 hour) → re-evaluation reflects new cycle
- [ ] E2E: rule pack upgrade → replay produces documented diff
- [ ] Run E2E against staging environment on release candidate

### Load & performance tests

- [ ] Define SLOs: p95 evaluation latency, p95 API read latency, ingest throughput
- [ ] Load test: 10K drivers, evaluate fleet compliance batch — measure duration
- [ ] Load test: sustained ingest at N events/sec for 30 minutes
- [ ] Load test: API read 500 req/sec on driver compliance endpoint
- [ ] Profile rule engine hot paths; optimize if SLO missed
- [ ] Document capacity baseline and scaling triggers

### Chaos & resilience tests

- [ ] Provider API timeout: sync retries, does not corrupt state
- [ ] Provider API partial response: handled gracefully
- [ ] Database connection loss: API returns 503, recovers on reconnect
- [ ] Duplicate webhook delivery: idempotent handling verified
- [ ] Evaluation with 4-hour gap in logs: policy applied correctly (flag vs fail)

### Manual / UAT

- [ ] Create UAT test script for design partners
- [ ] Compare Sentinel violations vs ELD-reported violations for sample week
- [ ] Document acceptable discrepancy threshold (target: zero for standard rules)
- [ ] Sign-off checklist for design partner before beta

### Release quality process

- [ ] Define release checklist: tests green, golden updated, changelog, migration tested
- [ ] Staging soak period (minimum 48h) before production promote
- [ ] Rollback procedure tested (see [07 — Infrastructure](./07-infrastructure-and-devops.md))

---

## Success Criteria

- 100% of golden fixtures pass on every merge to main
- Zero known false-negative violations in design partner UAT sample
- Load test meets defined SLOs at 2x expected launch fleet size
- Tenant isolation tests pass; no security test failures open at launch

---

## Open Questions

1. Acceptable discrepancy vs native ELD compliance output (100% match or documented exceptions)?
2. Run load tests on every release or nightly only?
3. Who owns golden fixture approval when intentional rule behavior changes?

---

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
