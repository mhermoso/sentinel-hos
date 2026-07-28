# 7. Infrastructure & DevOps

Deploy, operate, and observe Sentinel HOS reliably across dev, staging, and production.

**Depends on:** [01 — Foundation](./01-foundation-and-decisions.md)  
**Parallel with:** All engineering workstreams; hardening gates launch

---

## Things to Consider

### Environment strategy

| Environment | Purpose | Data | Access |
|-------------|---------|------|--------|
| Local | Developer machines | Synthetic / docker seed | Developer |
| Dev | Shared integration | Synthetic, reset frequently | Engineering |
| Staging | Pre-prod validation | Anonymized prod-like | Engineering + partners |
| Production | Customer workloads | Real customer data | Restricted |

- **Parity** — Staging should mirror production topology (same services, smaller instances).
- **Isolation** — Production credentials never used in dev/staging; separate cloud accounts or projects recommended.

### Deployment models

- **SaaS (multi-tenant)** — Single deployment serves all customers; strongest tenant isolation requirements.
- **Single-tenant (dedicated)** — Per-customer deployment for enterprise; higher ops cost, simpler isolation story.
- **Hybrid** — SaaS default + dedicated tier for large fleets. Decide v1 model early (see [01 — Foundation](./01-foundation-and-decisions.md)).

### Infrastructure as Code

- All resources defined in code (Terraform, Pulumi, CDK); no click-ops for prod.
- State stored remotely with locking (S3 + DynamoDB, Terraform Cloud, etc.).
- Environment differences via variables/modules, not manual edits.

### Observability pillars

- **Logs** — Structured JSON; correlation IDs across ingest → evaluate → API
- **Metrics** — RED method (Rate, Errors, Duration) for API; USE for workers
- **Traces** — Distributed tracing for request flows (OpenTelemetry)
- **Alerts** — Page on SLO burn, not on every error

### Key metrics to track

| Metric | Why |
|--------|-----|
| `evaluation_duration_seconds` | Rule engine performance |
| `ingest_events_total` | Throughput, provider health |
| `sync_errors_total` | Provider integration failures |
| `violations_detected_total` | Business signal, anomaly detection |
| `api_request_duration_seconds` | Customer experience |
| `webhook_delivery_failures_total` | Integration reliability |
| `data_quality_issues_total` | Ingest health |

---

## Tasks to Complete

### Cloud & networking

- [ ] Select cloud provider and create organization/account structure
- [ ] Create separate projects/accounts for dev, staging, production
- [ ] Define VPC/network topology: public subnets (LB), private subnets (app, DB)
- [ ] Configure security groups / firewall rules (least privilege)
- [ ] Set up DNS and TLS certificates (ACM, Let's Encrypt)
- [ ] Configure WAF or DDoS protection on public endpoints
- [ ] Document network diagram

### Infrastructure as Code

- [ ] Initialize IaC repository/module structure
- [ ] Define modules: network, database, compute, queue, secrets, monitoring
- [ ] Implement dev environment stack
- [ ] Implement staging environment stack (prod-like)
- [ ] Implement production environment stack
- [ ] CI plan/apply with approval gate for production
- [ ] Document IaC bootstrap and teardown procedures

### Compute & services

- [ ] Choose deployment target: Kubernetes (EKS/GKE), ECS, Cloud Run, VMs
- [ ] Containerize application services (Dockerfile, multi-stage build)
- [ ] Define service topology: API, worker (sync/evaluate), optional scheduler
- [ ] Configure auto-scaling policies (CPU, queue depth, request rate)
- [ ] Configure health check endpoints (`/health`, `/ready`)
- [ ] Set resource limits (CPU, memory) per service
- [ ] Define graceful shutdown (drain in-flight requests)

### Database

- [ ] Provision PostgreSQL (managed: RDS, Cloud SQL, etc.)
- [ ] Enable automated backups with point-in-time recovery
- [ ] Configure connection pooling (PgBouncer or managed pooler)
- [ ] Set up migration tool (Flyway, Alembic, golang-migrate, etc.)
- [ ] Define migration review process (no destructive migrations without plan)
- [ ] Test rollback procedure for last N migrations
- [ ] Configure read replica if needed for reporting (post-v1 OK)

### Message queue / workers

- [ ] Provision queue (SQS, RabbitMQ, Kafka — per ADR)
- [ ] Implement worker deployment for sync and evaluation jobs
- [ ] Configure dead-letter queues and alerting on DLQ depth
- [ ] Implement job retry with exponential backoff
- [ ] Monitor queue lag and scale workers accordingly

### Secrets management

- [ ] Provision secrets manager (AWS Secrets Manager, Vault, etc.)
- [ ] Inject secrets at runtime (never bake into images)
- [ ] Rotate database credentials on schedule
- [ ] Document secret rotation runbook
- [ ] Audit secret access

### CI/CD pipeline

- [ ] CI on PR: lint, unit tests, golden tests, integration tests, security scan
- [ ] Build and push container images on merge to main
- [ ] Deploy to dev automatically on merge
- [ ] Deploy to staging on release tag or manual promote
- [ ] Deploy to production with manual approval + change ticket
- [ ] Implement blue/green or rolling deployment strategy
- [ ] Automated smoke test post-deploy (health + sample API call)
- [ ] Document rollback: redeploy previous image tag

### Observability — logging

- [ ] Structured JSON logging library configured
- [ ] Correlation ID propagated: API request → worker → evaluation
- [ ] Centralized log aggregation (CloudWatch, Datadog, ELK)
- [ ] Log retention policy (30–90 days hot, archive longer for audit)
- [ ] PII scrubbing in logs (no full driver names in debug logs unless required)

### Observability — metrics & tracing

- [ ] Export Prometheus metrics or vendor equivalent (Datadog, CloudWatch)
- [ ] Create dashboards: API health, ingest pipeline, rule engine, webhooks
- [ ] Implement OpenTelemetry tracing for API and workers
- [ ] Define SLOs and error budgets (e.g. 99.9% API availability)
- [ ] Configure alerting rules tied to SLOs

### Alerting & on-call

- [ ] Integrate PagerDuty/Opsgenie/on-call rotation
- [ ] Define alert severity: page (P1), ticket (P2), log (P3)
- [ ] P1 alerts: API down, DB unreachable, evaluation pipeline stalled > N min
- [ ] P2 alerts: sync failure rate spike, webhook delivery failures, DLQ depth
- [ ] Runbook linked from each alert
- [ ] Test paging pipeline before launch

### Backup & disaster recovery

- [ ] Define RPO (max data loss) and RTO (max downtime) targets
- [ ] Automated daily DB backups with 30-day retention minimum
- [ ] Test restore procedure quarterly (document last test date)
- [ ] Document disaster recovery runbook: region failure, DB corruption
- [ ] Cross-region backup replication (if SLA requires)

### Rate limiting & edge protection

- [ ] API gateway or load balancer rate limiting per org/API key
- [ ] Request size limits
- [ ] Timeout configuration (client, server, DB)
- [ ] CORS and TLS termination at edge

### Cost management

- [ ] Tag all resources (env, service, cost-center)
- [ ] Set billing alerts at 50/80/100% of monthly budget
- [ ] Right-size instances after load test baseline
- [ ] Document cost estimate per 1K drivers/month

---

## Success Criteria

- One-command (or one-pipeline) deploy to staging and production
- Restore from backup tested successfully within RTO target
- On-call receives test page and can follow runbook to resolve simulated incident
- Dashboards show API, ingest, and evaluation health at a glance
- No manual SSH-to-prod for routine operations

---

## Open Questions

1. Kubernetes vs managed PaaS (Cloud Run, Fly.io) for v1 ops simplicity?
2. Single region for launch, or multi-region from day one?
3. Who is on-call at alpha/beta — founding team only, or dedicated rotation?
4. Target monthly infra cost cap for pre-revenue stage?

---

[← Back to Launch Checklist](../../LAUNCH_CHECKLIST.md)
