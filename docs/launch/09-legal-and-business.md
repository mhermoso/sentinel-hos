# 9. Legal & Business

Legal, commercial, and liability foundations required before external customers use Sentinel HOS with real driver data.

**Depends on:** [01 — Foundation](./01-foundation-and-decisions.md) (product scope informs legal)  
**Required before:** [10 — Launch Readiness](./10-launch-readiness.md) general availability

---

## Things to Consider

### Product liability boundary

- Sentinel HOS is **compliance tooling**, not legal advice.
- Carriers remain responsible for driver compliance under FMCSA regulations.
- Product must not imply certification as an ELD unless pursuing FMCSA ELD certification (separate, heavy process).
- Disclaimers must be clear in ToS, marketing, and in-app where violations are displayed.

### Licensing model

| Model | Pros | Cons |
|-------|------|------|
| Proprietary SaaS | Revenue, control | No community contributions |
| Open source (engine) | Trust, auditability | Harder to monetize |
| Dual license | OSS engine + commercial support | Legal complexity |
| Source-available | Middle ground | Unclear ecosystem |

- Current README says **License: TBD** — resolve before public repo or external contributors.

### Data processing roles

- **Controller vs processor** — Fleet operator typically controls driver data; Sentinel HOS processes on their behalf.
- **DPA (Data Processing Agreement)** — Required for GDPR and expected by enterprise customers.
- **Subprocessors** — Cloud provider, email, analytics, support tools must be listed and customer-notifiable on change.

### Commercial agreements

- **MSA (Master Service Agreement)** — Governs overall relationship
- **SLA** — Uptime commitment, support response times, credits for breach
- **Order form** — Pricing, driver count, term, plan tier
- **BAA** — Only if handling HIPAA data (unlikely for standard HOS)

### FMCSA-specific considerations

- HOS records must be retained **6 months** (ELD rule); product retention policy must meet or exceed.
- Understand difference between **ELD provider certification** and **third-party compliance analytics**.
- If exporting data for roadside inspections or audits, format and completeness matter for customer workflows.

### Pricing (if SaaS)

Common models in fleet/telematics:
- Per active driver / month
- Per vehicle / month
- Tiered by fleet size (volume discounts)
- Platform fee + per-driver
- Enterprise custom (annual contract, dedicated deployment)

- **v1 recommendation:** Simple per-driver pricing for beta; refine after design partner feedback.

---

## Tasks to Complete

### Entity & IP

- [ ] Confirm legal entity for contracting (LLC, Corp)
- [ ] Assign IP ownership for code (founder assignment, contractor agreements)
- [ ] Trademark search for "Sentinel HOS" (or final product name)
- [ ] Register domain(s) and social handles
- [ ] Decide open source vs proprietary; update LICENSE file in repo

### Terms & policies (legal review required)

- [ ] Draft Terms of Service (ToS)
- [ ] Draft Privacy Policy
- [ ] Draft Acceptable Use Policy (AUP)
- [ ] Draft Cookie Policy (if applicable)
- [ ] Draft Data Processing Agreement (DPA) template
- [ ] Include compliance disclaimer in ToS: not legal advice, not ELD certification (unless pursuing)
- [ ] Define minimum age / eligibility for account holders
- [ ] Publish policies on website before beta signup

### Customer agreements

- [ ] Draft Master Service Agreement (MSA) template
- [ ] Draft SLA document: uptime target (e.g. 99.9%), support tiers, credit schedule
- [ ] Draft Order Form / Subscription Agreement template
- [ ] Define beta agreement addendum (limited warranty, feedback license, no SLA)
- [ ] Define enterprise addendum (dedicated infra, CMEK, custom DPA terms)

### Liability & disclaimers

- [ ] Product disclaimer: carrier retains compliance responsibility
- [ ] Accuracy disclaimer: based on data provided by customer and ELD providers
- [ ] Limitation of liability caps (negotiate standard vs enterprise)
- [ ] Indemnification clauses (mutual, scope defined)
- [ ] In-app disclaimer on violation views (optional but recommended)
- [ ] Marketing review: no claims of "guaranteed compliance" or "FMCSA certified" unless true

### Privacy & regulatory

- [ ] Complete subprocessor list with legal names and data processed
- [ ] Define data residency commitments per plan/region
- [ ] Implement customer data export and deletion (see [05 — Security](./05-security-privacy-and-compliance.md))
- [ ] Register for state privacy laws if applicable (CCPA service provider list, etc.)
- [ ] GDPR representative if serving EU without EU entity (if applicable)

### Insurance

- [ ] Obtain general liability insurance
- [ ] Obtain cyber liability / E&O insurance (recommended before handling customer PII at scale)
- [ ] Review insurance requirements in enterprise customer contracts

### Pricing & packaging

- [ ] Define beta pricing (free, discounted, or paid pilot)
- [ ] Define v1 GA pricing tiers: features, driver limits, support level
- [ ] Build pricing page (even if "contact us" for enterprise)
- [ ] Define billing system (Stripe Billing, Chargebee, manual invoicing for beta)
- [ ] Define refund/cancellation policy
- [ ] Tax handling: sales tax/VAT collection strategy

### Sales & onboarding (business ops)

- [ ] Define ideal customer profile (ICP): fleet size, industry, ELD vendor
- [ ] Create sales one-pager / deck
- [ ] Define beta application process and qualification criteria
- [ ] Customer onboarding checklist: contract, DPA signed, provider connected, UAT
- [ ] Define customer success touchpoints for beta (weekly check-in, etc.)

### Compliance certifications (roadmap)

- [ ] Document SOC 2 timeline and budget (see [05 — Security](./05-security-privacy-and-compliance.md))
- [ ] Track FMCSA ELD certification requirement — confirm out of scope unless strategic decision to pursue
- [ ] Maintain compliance calendar: policy review dates, insurance renewal, SOC 2 audit

---

## Success Criteria

- Legal counsel has reviewed ToS, Privacy Policy, and DPA before first paying customer
- LICENSE file in repo matches business model
- Beta customers sign beta agreement covering data use and liability
- Pricing and packaging documented internally; communicated to design partners
- No marketing claims that exceed product capabilities or certifications

---

## Open Questions

1. Open-source the rule engine for transparency, or keep fully proprietary?
2. Free beta vs paid pilot — which attracts better design partners?
3. Pursue FMCSA ELD certification long-term, or stay analytics/compliance layer only?
4. US-only legal entity for launch, or plan for EU/customer global from start?

---

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
