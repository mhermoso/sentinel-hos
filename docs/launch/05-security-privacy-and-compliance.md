# 5. Security, Privacy & Compliance (Product)

Protect driver PII, location data, and fleet operational data. Design for future SOC 2 and customer security questionnaires from day one.

**Depends on:** [01 — Foundation](./01-foundation-and-decisions.md)  
**Parallel with:** All engineering workstreams

---

## Things to Consider

### Data sensitivity

| Data type | Examples | Sensitivity | Regulatory touchpoints |
|-----------|----------|-------------|------------------------|
| PII | Driver name, license, phone | High | GDPR, CCPA, state privacy laws |
| Location | GPS coordinates, routes | High | Driver privacy, stalking concerns |
| HOS logs | Duty status, timestamps | High | FMCSA retention (6 months minimum for ELD records) |
| Credentials | ELD API keys | Critical | Customer trust, breach impact |
| Aggregated fleet stats | Violation rates | Medium | Competitive sensitivity |

- **Minimize collection** — Only store fields needed for compliance evaluation and audit.
- **Purpose limitation** — Do not use driver data for unrelated analytics without consent.

### Threat model (initial)

- **External attacker** — API abuse, credential theft, SQL injection, tenant isolation bypass
- **Malicious insider** — Employee accessing customer driver data without authorization
- **Customer misconfiguration** — Exposed API keys, weak webhook verification
- **Provider compromise** — Forged webhook payloads from spoofed ELD vendor
- **Supply chain** — Vulnerable dependencies in rule engine or API

### Encryption

- **In transit** — TLS 1.2+ everywhere; HSTS on public endpoints
- **At rest** — Database encryption (AES-256); encrypted object storage for raw payloads
- **Key management** — Cloud KMS (AWS KMS, GCP Cloud KMS); no keys in source code
- **Secrets rotation** — Provider credentials and API keys rotatable without downtime

### Access control layers

1. **Network** — VPC, private subnets for DB, no public DB endpoints
2. **Application** — RBAC, org scoping, least privilege service accounts
3. **Database** — Row-level security where supported
4. **Human access** — No production DB access by default; break-glass with approval and logging

### Compliance frameworks (roadmap)

- **SOC 2 Type II** — Expected by enterprise fleet customers; 6–12 month process
- **GDPR / CCPA** — If any EU/CA drivers or customers; data subject rights (access, delete)
- **FMCSA data handling** — Understand retention and audit requirements for HOS records
- **PCI** — Not applicable unless processing payments in-product (use Stripe etc.)

### Incident response

- **Detection** — Alert on anomalous API access, failed auth spikes, data export volume
- **Containment** — Revoke compromised keys, isolate affected tenant
- **Notification** — Customer notification SLA (e.g. 72 hours); regulatory if PII breach
- **Post-mortem** — Blameless review, action items tracked

---

## Tasks to Complete

### Data governance

- [ ] Create data classification document (public, internal, confidential, restricted)
- [ ] Map each schema field to classification level
- [ ] Define data retention policy per classification (HOS logs: minimum 6 months, default 2 years — confirm legal)
- [ ] Define data deletion procedure for customer offboarding (hard delete vs anonymize)
- [ ] Define data subject access request (DSAR) process for GDPR/CCPA
- [ ] Document lawful basis for processing (contract, legitimate interest) in privacy policy draft

### Encryption & secrets

- [ ] Enforce TLS on all public endpoints; configure HSTS
- [ ] Enable database encryption at rest
- [ ] Encrypt raw provider payload storage (S3 SSE-KMS or equivalent)
- [ ] Integrate secrets manager for provider credentials and internal secrets
- [ ] Ban secrets in git: pre-commit hook (gitleaks, trufflehog)
- [ ] Document key rotation runbook for API keys and provider credentials
- [ ] Encrypt backups

### Authentication & access control

- [ ] Implement secure API key generation (high entropy, prefix for identification)
- [ ] Hash stored API keys (show once on creation)
- [ ] Implement session/token expiration and refresh policies
- [ ] Enforce RBAC on all endpoints (see [04 — API](./04-api-and-platform.md))
- [ ] Implement tenant isolation tests (automated: org A cannot read org B)
- [ ] Disable shared production credentials; individual SSO for engineering access
- [ ] Configure break-glass production access with approval workflow and session logging

### Audit logging

- [ ] Log all authentication events (success/failure, IP, user agent)
- [ ] Log admin actions: settings changes, credential updates, bulk exports
- [ ] Log data access for sensitive endpoints (optional: full read audit for enterprise tier)
- [ ] Ship audit logs to immutable storage (WORM bucket or SIEM)
- [ ] Define audit log retention (minimum 1 year recommended)
- [ ] Ensure audit logs contain no secrets or full PII payloads

### Application security

- [ ] Input validation on all API endpoints (schema validation)
- [ ] Parameterized queries / ORM only — no raw SQL with user input
- [ ] CSRF protection on cookie-based sessions (if UI uses cookies)
- [ ] CORS policy: explicit allowlist, no wildcard in production
- [ ] Content Security Policy for admin UI
- [ ] Dependency scanning in CI (Dependabot, Snyk, or equivalent)
- [ ] Static analysis (Semgrep, CodeQL) on PRs
- [ ] Annual or pre-launch penetration test

### Webhook & ingest security

- [ ] Verify inbound provider webhook signatures where supported
- [ ] IP allowlist for provider webhook sources (if documented by vendor)
- [ ] Sign outbound customer webhooks (HMAC)
- [ ] Rate limit unauthenticated ingest endpoints
- [ ] Validate payload size limits

### Privacy

- [ ] Draft privacy policy (legal review required)
- [ ] Implement customer data export API (GDPR portability)
- [ ] Implement customer data deletion API with confirmation and audit trail
- [ ] Document subprocessors list (cloud provider, email, monitoring tools)
- [ ] Cookie/consent banner if admin UI uses non-essential cookies

### SOC 2 readiness (pre-certification)

- [ ] Document security policies: access control, change management, incident response
- [ ] Enable MFA for all engineering and admin accounts
- [ ] Document vendor risk assessment process for third-party tools
- [ ] Implement change management: PR reviews, CI gates, deployment approvals for prod
- [ ] Centralized logging and alerting for security events
- [ ] Engage SOC 2 auditor when customer pipeline requires it (typically post-alpha)

### Incident response

- [ ] Write incident response plan: roles, severity levels, communication templates
- [ ] Define on-call rotation and escalation path
- [ ] Create security incident runbook (credential leak, suspected breach, DDoS)
- [ ] Conduct tabletop exercise before production launch
- [ ] Prepare customer notification template for data incidents

---

## Success Criteria

- Automated tenant isolation tests pass in CI
- No secrets in repository history (verified by scan)
- Pen test or internal security review completed with critical/high findings resolved
- Privacy policy and data retention documented before first external customer
- Engineering can execute credential rotation runbook without service outage

---

## Open Questions

1. Target SOC 2 Type II before GA, or after first enterprise deal?
2. Store GPS/location data, or strip location and keep only duty status + timestamps?
3. EU data residency required for any early customers?
4. Customer-managed encryption keys (CMEK) required for enterprise tier?

---

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
