# 2. Core Rule Engine

The deterministic heart of Sentinel HOS. Evaluates driver duty status against regulatory rule packs and produces auditable compliance outcomes.

**Depends on:** [01 — Foundation & Decisions](./01-foundation-and-decisions.md)  
**Blocks:** [04 — API & Platform](./04-api-and-platform.md), [06 — Testing & Quality](./06-testing-and-quality.md)

---

## Things to Consider

### Rule engine design

- **Pure functions preferred** — `(canonical_events, rule_pack, as_of_time) → ComplianceResult`. Side effects (DB writes, alerts) belong outside the engine.
- **Rule pack isolation** — FMCSA v1 must not share code paths with future Canada/Mexico packs in ways that create hidden coupling.
- **Incremental vs full recompute** — Full recompute is simpler and more auditable; incremental is faster but harder to verify. Start with full recompute for a driver's window.
- **Lookback windows** — Evaluations need sufficient history (at least 8 days for 70-hour rule, more for disputes). Define minimum history required per rule.
- **"As of" semantics** — Compliance at `now` vs compliance projected at a future dispatch time are different queries; both may be needed.

### FMCSA rules to implement (property-carrying, standard)

| Rule | Limit | Notes |
|------|-------|-------|
| 11-hour driving | Max 11 hours driving after 10 consecutive hours off duty | Excludes adverse driving extension in v1 unless scoped |
| 14-hour window | No driving after 14th hour after coming on duty | Window does not pause for off-duty breaks (except sleeper split rules) |
| 30-minute break | Required before 8 hours cumulative driving | Driving time since last 30+ min off/sleeper |
| 60/70-hour rule | Max 60 hrs on-duty in 7 days or 70 in 8 days | Carrier elects 7- or 8-day cycle |
| 10-hour off-duty | Required before driving again after hitting driving/window limits | |
| 34-hour restart | Resets 60/70-hour cycle | Two consecutive 1–5 AM periods |
| Sleeper berth split | 7/3 or 8/2 split in sleeper | Pauses 14-hour clock per qualifying split |
| 24-hour period | Defines daily reset boundaries | Often midnight driver home terminal time |

### Violation types

- **Current violation** — Driver is actively out of compliance (e.g. driving past 11 hours).
- **Historical violation** — Occurred in the past; may still matter for audits and CSA scores.
- **Approaching limit** — Configurable thresholds (e.g. 30 minutes remaining on 11-hour rule) for proactive alerts.
- **Potential violation** — Projected if driver continues current activity (useful for dispatch planning).

### Edge cases that break naive implementations

- **DST transitions** — Spring forward / fall back affects 24-hour periods and 34-hour restart windows.
- **Midnight boundaries** — 24-hour period reset vs calendar day.
- **Gaps in log data** — Missing segments: assume on-duty? reject evaluation? flag as data quality issue?
- **Overlapping events** — Two statuses at once from provider error.
- **Personal conveyance / yard move** — May not count as driving depending on rule interpretation.
- **Split sleeper pairing** — Valid 7/3 and 8/2 combinations; invalid splits must not pause the 14-hour clock.
- **Cycle change mid-period** — Carrier switches 60 vs 70-hour cycle (rare but must not corrupt history).

### Audit & dispute support

- Regulators and carriers dispute HOS violations months later. Every evaluation must be reproducible.
- Store: rule pack version, normalized input snapshot (or event IDs + versions), output, evaluator version, timestamp.
- Support "explain this violation" — human-readable trace of which rule fired and which events contributed.

---

## Tasks to Complete

### Rule pack framework

- [ ] Define `RulePack` interface: metadata, version, applicable jurisdictions, rule set
- [ ] Define `Rule` interface: evaluate(context) → RuleResult
- [ ] Define shared evaluation context: driver profile, carrier settings (7 vs 8 day), events, as_of time
- [ ] Implement rule pack registry and loader (file-based or DB-backed)
- [ ] Implement rule pack version pinning per organization (carriers may stay on older pack until validated)
- [ ] Add rule pack validation on load (schema check, required rules present)

### Input preparation

- [ ] Implement event normalizer: sort by timestamp, merge adjacent same-status segments (if policy allows)
- [ ] Implement gap detector and gap handling policy (flag, interpolate, or fail)
- [ ] Implement overlap detector and resolution policy
- [ ] Build `EvaluationWindow` builder: fetch events from `as_of - lookback` to `as_of`
- [ ] Unit-test normalizer with messy real-world fixture data

### FMCSA rule pack v1

- [ ] Implement 10-hour off-duty requirement checker
- [ ] Implement 11-hour driving limit rule
- [ ] Implement 14-hour on-duty window rule
- [ ] Implement 30-minute break rule (8-hour driving threshold)
- [ ] Implement 60-hour / 7-day on-duty cycle rule
- [ ] Implement 70-hour / 8-day on-duty cycle rule
- [ ] Implement 34-hour restart detection and cycle reset
- [ ] Implement sleeper berth split rules (7/3 and 8/2)
- [ ] Implement 24-hour period / daily reset logic with home terminal timezone
- [ ] Implement carrier configuration: 60 vs 70-hour cycle selection
- [ ] Document each rule with CFR citation and worked examples

### Violation & warning engine

- [ ] Define `Violation` model: type, severity, start/end time, rule reference, contributing events
- [ ] Define `Warning` model for approaching limits (configurable thresholds per org)
- [ ] Implement violation aggregation: dedupe overlapping violations of same type
- [ ] Implement historical violation scan over date range
- [ ] Implement projection mode: "if driver drives until X, will they violate?"
- [ ] Expose violation severity levels (critical, warning, info) for downstream alerting

### Rest & break logic

- [ ] Implement qualifying break detector (30+ consecutive minutes off-duty or sleeper)
- [ ] Implement off-duty period calculator (10-hour, 34-hour)
- [ ] Implement sleeper berth period calculator with split pairing
- [ ] Implement driving time accumulator since last break
- [ ] Implement on-duty time accumulator for 60/70-hour rolling windows

### Audit trail

- [ ] Define `EvaluationRecord` schema: id, driver_id, as_of, rule_pack_version, input_hash, results, created_at
- [ ] Persist evaluation records (append-only)
- [ ] Implement evaluation replay CLI: `replay --driver X --date Y --rule-pack Z`
- [ ] Implement explain/trace output for a single violation (which events, which rule, math shown)
- [ ] Hash canonical inputs for integrity verification

### Engine API (internal)

- [ ] `evaluate_driver(driver_id, as_of_time) → ComplianceSnapshot`
- [ ] `evaluate_driver_range(driver_id, start, end) → []Violation`
- [ ] `project_driver(driver_id, hypothetical_events) → ComplianceSnapshot`
- [ ] `get_available_drive_time(driver_id, as_of) → DriveTimeRemaining`
- [ ] Document internal API with examples

### Determinism & golden tests

- [ ] Create golden fixture directory with 20+ scenarios (simple, edge, dispute-style)
- [ ] CI gate: golden tests must pass on every PR touching engine
- [ ] Cross-run determinism test: same input 1000 times → identical output
- [ ] Rule pack upgrade test: same input, old vs new pack → documented diff

---

## Success Criteria

- Engine passes all golden fixtures with documented expected outcomes
- Any violation can be explained with a human-readable trace
- Evaluation of a single driver completes in < 100ms for 8 days of events (target; tune as needed)
- Replay from stored inputs reproduces historical evaluation bit-for-bit

---

## Open Questions

1. Include adverse driving conditions exception in v1?
2. Support personal conveyance and yard move as distinct statuses?
3. How to handle incomplete logs — fail closed (assume violation) or fail open (flag uncertainty)?
4. Should approaching-limit thresholds be per-org configurable or global defaults?

---

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
