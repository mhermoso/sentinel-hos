# 10. Launch Readiness

Validate the product with real fleets, define go/no-go criteria, and execute a controlled rollout to general availability.

**Depends on:** All prior workstreams ([01](./01-foundation-and-decisions.md) through [09](./09-legal-and-business.md))  
**Goal:** Confident, supported production launch

---

## Things to Consider

### Launch phases

```
Internal dogfood → Alpha (design partners) → Closed Beta → Open Beta → GA
```

| Phase | Users | Stability | Support | SLA |
|-------|-------|-----------|---------|-----|
| Alpha | 1–2 design partners | Breaking changes OK | White-glove, daily contact | None |
| Closed Beta | 5–10 fleets | Feature-complete v1 | Email + scheduled calls | Best-effort |
| Open Beta | Invite-only, larger | Stable API | Ticket system | Soft SLA |
| GA | Public signup | Production-grade | Tiered support | Contractual SLA |

- **Do not skip alpha** — HOS accuracy must be validated against real ELD data before scaling.

### Go/no-go criteria mindset

- Launch is a decision, not a date. Criteria should be objective and pre-agreed.
- **Blockers:** False-negative violations in UAT, tenant isolation failure, no incident runbook, no legal docs.
- **Non-blockers for beta:** Admin UI polish, SOC 2 cert, third provider adapter.

### Support model evolution

- **Alpha:** Founding team direct Slack/phone with design partners
- **Beta:** Shared support email, 1-business-day response target
- **GA:** Tiered support (standard vs enterprise), documented escalation path

### Success metrics (product)

| Metric | Alpha target | GA target |
|--------|--------------|-----------|
| Violation accuracy vs ELD | ≥ 99% match on sample | ≥ 99.5% |
| API uptime | 95% (best effort) | 99.9% contractual |
| Evaluation latency p95 | < 500ms | < 200ms |
| Sync freshness | < 15 min | < 5 min (or real-time) |
| Design partner NPS | Qualitative feedback | > 40 |

### Rollout strategy

- **Feature flags** — Enable new rules or providers per org before global rollout
- **Canary deploys** — Route 5% traffic to new version; monitor error rate before full promote
- **Rule pack rollout** — Pin existing customers to current pack; opt-in to new pack after validation

### Post-launch

- 30/60/90-day review: incidents, accuracy disputes, feature requests, churn signals
- Prioritize: additional providers, jurisdictions, dispatch integrations, mobile

---

## Tasks to Complete

### Pre-alpha checklist

- [ ] Rule engine passes full golden suite
- [ ] One provider adapter working end-to-end in staging
- [ ] API core endpoints functional with auth
- [ ] Design partner identified and agreement signed (beta addendum)
- [ ] Anonymized or real test data pipeline agreed with partner
- [ ] Internal dogfood: team runs synthetic fleet through full flow

### Alpha program

- [ ] Define alpha scope document shared with design partner (features, limitations, data use)
- [ ] Onboard design partner org: credentials, provider connection, driver sync
- [ ] Run parallel comparison: Sentinel violations vs ELD native reports (minimum 2 weeks of data)
- [ ] Weekly feedback sessions with design partner; track issues in backlog
- [ ] Fix all P0 accuracy issues before beta
- [ ] Document discrepancies: bug vs known limitation vs out-of-scope rule
- [ ] Alpha exit report: accuracy metrics, open issues, beta readiness recommendation

### Beta criteria (define before alpha ends)

- [ ] **Stability:** No P0 bugs open; P1 bugs have workarounds documented
- [ ] **Accuracy:** ≥ 99% violation match vs ELD on validation dataset
- [ ] **Latency:** API p95 and evaluation latency meet staging SLOs
- [ ] **Security:** Tenant isolation tests pass; no critical security findings open
- [ ] **Legal:** ToS, Privacy Policy, DPA published; beta agreement template ready
- [ ] **Docs:** API docs, setup guide, provider guide complete for v1
- [ ] **Support:** Support email active; escalation path to engineering defined
- [ ] **Ops:** On-call rotation staffed; runbooks tested; monitoring dashboards live
- [ ] Sign-off meeting: engineering, product, legal, ops

### Closed beta execution

- [ ] Recruit 5–10 beta fleets matching ICP (see [09 — Legal & Business](./09-legal-and-business.md))
- [ ] Beta onboarding playbook: contract → connect ELD → validate sync → UAT → go-live
- [ ] Track beta health dashboard: active orgs, drivers synced, violations/day, error rate
- [ ] Bi-weekly beta newsletter: known issues, upcoming features, feedback requests
- [ ] Collect structured feedback (survey + interviews) at beta midpoint and end
- [ ] Iterate on top 3 beta pain points before GA

### Production launch criteria (GA go/no-go)

- [ ] All beta exit criteria met at production scale (load tested at 2x beta fleet size)
- [ ] SOC 2 in progress or complete (per customer pipeline requirement)
- [ ] Penetration test complete; critical/high findings resolved
- [ ] Production environment deployed with backups, monitoring, on-call verified
- [ ] SLA and support tiers documented and contract-ready
- [ ] Pricing live; billing system tested with real payment (or enterprise invoice flow)
- [ ] Marketing site live: product overview, pricing, docs link, trust/security page
- [ ] Launch comms prepared: blog post, email to beta users, partner announcements
- [ ] Rollback tested in production-like environment within last 30 days
- [ ] Go/no-go meeting with explicit attendee sign-off list

### Support channels

- [ ] Support email (support@...) monitored during business hours minimum
- [ ] Ticketing system (Zendesk, Linear, GitHub Issues — pick one)
- [ ] Define severity definitions: P0 (compliance wrong), P1 (service down), P2 (degraded), P3 (question)
- [ ] Define response time targets per severity and plan tier
- [ ] Create FAQ from alpha/beta common questions
- [ ] Escalation path: support → on-call engineer → founder (documented)
- [ ] Status page (status.sentinel-hos.com): incidents and maintenance windows

### Monitoring dashboards (launch day)

- [ ] Executive dashboard: active orgs, drivers, API uptime, violation volume
- [ ] Engineering dashboard: error rates, latency, queue depth, sync failures
- [ ] Business dashboard: signups, activations, churn, support ticket volume
- [ ] Launch day war room: dedicated channel, all leads available, hourly check first 24h

### Launch execution

- [ ] Freeze non-critical merges 48h before GA deploy
- [ ] Deploy to production with canary/rolling strategy
- [ ] Run automated smoke tests post-deploy
- [ ] Enable public signup or sales-led onboarding (per GTM decision)
- [ ] Monitor dashboards for 72 hours elevated attention
- [ ] Send launch communications
- [ ] Hold launch retrospective within 1 week

### Post-launch iteration plan

- [ ] Publish 90-day roadmap: providers, rules, features prioritized from beta feedback
- [ ] Schedule monthly business review: metrics, incidents, customer feedback
- [ ] Define process for rule pack updates (customer communication, replay, opt-in)
- [ ] Plan provider #2 and #3 based on beta ELD vendor distribution
- [ ] Plan jurisdiction expansion (Canada, intrastate variants) based on customer demand
- [ ] Collect case study from successful beta partner (with permission)

---

## Success Criteria

- Alpha validates ≥ 99% violation accuracy on design partner data
- Beta fleets actively using API or UI weekly without engineering intervention
- GA launch completes with zero P0 incidents in first 72 hours
- Support tickets answered within defined SLA for first month
- Post-launch retrospective produces prioritized 90-day roadmap

---

## Launch Day Checklist (printable)

- [ ] Production deploy successful; smoke tests green
- [ ] Monitoring dashboards open in war room
- [ ] On-call engineer identified and reachable
- [ ] Support inbox monitored
- [ ] Status page ready (no active incidents)
- [ ] Rollback command documented and tested
- [ ] Legal docs live on website
- [ ] Docs site accessible
- [ ] Launch comms sent
- [ ] Founding team available for 24h elevated response

---

## Open Questions

1. Public GA signup or sales-led only for first 6 months?
2. Geographic launch: US-only or North America?
3. Minimum fleet size for beta participants (too small = unrepresentative; too large = risk)?
4. Offer migration assistance for beta → GA pricing, or automatic conversion?

---

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
