# Sentinel HOS

Deterministic multi-provider compliance and automated Hours of Service (HOS) regulatory engine for fleet logistics.

## Overview

Sentinel HOS (also branded as **Driver Compliance Watch / DCW**) is a compliance platform designed for commercial fleet operators who must meet federal and jurisdictional HOS regulations. The engine evaluates driver duty status, rest periods, and driving limits using deterministic, auditable rules—so the same inputs always produce the same compliance outcome across providers and integrations.

## Key Capabilities

- **Multi-provider compliance** — Normalize and reconcile HOS data from ELDs, telematics platforms, and fleet management systems into a single compliance model.
- **Deterministic rule engine** — Apply regulatory logic (FMCSA and configurable jurisdictional rules) with reproducible, traceable results suitable for audits and disputes.
- **Automated HOS enforcement** — Detect violations, approaching limits, and required rest breaks in near real time.
- **Fleet logistics integration** — Support dispatch, safety, and operations workflows with actionable compliance signals.

## Problem Space

Fleet operators face fragmented ELD data, inconsistent interpretations of HOS rules, and manual review processes that do not scale. Sentinel HOS centralizes regulatory evaluation so safety teams, dispatchers, and compliance officers work from one authoritative source of truth.

## Status

Active development. The primary implementation lives in [`dcw-backend/`](./dcw-backend/):

| Area | State |
|------|--------|
| Architecture / ADRs | ADRs 001–006 accepted |
| Geotab ingestion | Live (poll + history seed) |
| Motive / Samsara | Adapter stubs |
| Rule pack | `fmcsa-us-property@1.3.0` |
| Notifier | Twilio Voice/SMS with alert locks (dry-run supported) |
| Dashboard | FastAPI REST + HTMX HOS timeline |
| Launch readiness | Pre-alpha — see [LAUNCH_CHECKLIST.md](./LAUNCH_CHECKLIST.md) |

Local quickstart: [`dcw-backend/README.md`](./dcw-backend/README.md).

## License

Functional Source License (FSL-1.1-Apache-2.0) — see [`dcw-backend/LICENSE`](./dcw-backend/LICENSE).
