# ADR-006: Source-Available Licensing (FSL 1.1) & SaaS Tier Monetization

## Status

Accepted

## Context

DCW requires a licensing and monetization strategy that:

1. Builds trust with fleet safety officers via public source transparency on GitHub.
2. Prevents competitors from offering our engine as a managed cloud service.
3. Directly monetizes enterprise fleets via a Per-Vehicle / Per-Month (PVPM) SaaS structure.

## Decision

1. **GitHub Repository Licensing**: Apply the **Functional Source License 1.1 (FSL-1.1-Apache-2.0)**. The code is public, but commercial hosting as a competing service is prohibited. Converts to Apache 2.0 after 2 years.
2. **Hosted SaaS Application**: Enforce a 3-tier SaaS pricing model (**STARTER** $8/truck, **PRO** $18/truck, **ENTERPRISE** Custom) guarded by `app/core/billing.py` feature gates:
* **STARTER**: Telematics ingestion, SMS alerts, web dashboard.
* **PRO**: Unlocks Twilio Voice IVR phone calls and daily WeasyPrint PDF executive reports.
* **ENTERPRISE**: Unlocks custom SLAs, high-frequency polling (30s), and SAML/SSO authentication.

## Consequences

* Protects commercial IP while maintaining source transparency for regulatory audits.
* Feature gating is enforced strictly at the API layer, returning HTTP `402 Payment Required` for unauthorized feature calls.
