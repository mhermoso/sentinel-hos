# Driver Compliance Watch (DCW) — SaaS Terms of Service

**Last Updated:** July 28, 2026

Welcome to Driver Compliance Watch ("DCW", "We", "Us"). These Terms of Service govern your fleet's access to and use of our fully managed cloud platform at `app.drivercompliancewatch.com`.

---

## 1. Subscription Tiers & Billing Structure

DCW is billed on a **Per-Vehicle / Per-Month (PVPM)** subscription basis:

| Plan Tier | Price (USD) | Included Capabilities & Limits |
| --- | --- | --- |
| **STARTER** | **$8 / truck / month** | Telematics Ingestion, Web Dashboard, Real-time SMS Alerts, Basic CSV Reports. |
| **PRO** | **$18 / truck / month** | Includes **STARTER** + Automated Twilio Voice IVR Phone Calls, Daily Executive PDF Audits, Priority Telematics Polling. |
| **ENTERPRISE** | **Custom Invoicing** | Includes **PRO** + Dedicated SLA Support, Custom Telematics API Integrations, SAML/SSO Authentication. |

---

## 2. Telematics & Usage Metering

* **Active Vehicle Count**: Monthly fees are metered based on the highest count of active vehicles connected to telematics APIs (Geotab, Motive, Samsara) during the billing cycle.
* **Quota Restrictions**: Voice IVR call quotas and automated PDF generation are strictly gated by your assigned Subscription Tier. Attempting to access Pro/Enterprise capabilities on a Starter plan will result in API HTTP `402 Payment Required` responses.

---

## 3. Data Privacy & Compliance

DCW operates as a read-only compliance evaluation overlay. We store raw telematics payloads and normalized HOS records strictly for regulatory evaluation and daily compliance audit reports under 49 CFR Part 395 guidelines.
