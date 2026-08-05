# DigitalOcean Environments — Runbook

How Driver Compliance Watch (DCW) is deployed on DigitalOcean App Platform, and how
to promote from **dev** to **staging** and **production**.

## Topology (per environment)

Each environment is one App Platform app plus two dedicated managed database clusters:

| Piece                | dev (live)                          | staging (planned)    | production (planned)     |
| -------------------- | ----------------------------------- | -------------------- | ------------------------ |
| App Platform app     | `dcw-dev`                           | `dcw-staging`        | `dcw-prod`               |
| Git branch           | `dev`                               | `staging`            | `main`                   |
| PostgreSQL 16        | `dcw-dev-pg` (1 node, 1 GB)         | 1 node, 2 GB         | 2+ nodes (HA standby)    |
| Valkey (Redis)       | `dcw-dev-redis` (1 node, 1 GB)      | 1 node, 1 GB         | 2 nodes (HA)             |
| API instance         | `apps-s-1vcpu-1gb` × 1              | same                 | `apps-s-1vcpu-2gb` × 2   |
| Worker instance      | `apps-s-1vcpu-0.5gb` × 1            | same                 | `apps-s-1vcpu-1gb` × 1   |
| `ALERT_DRY_RUN`      | `true`                              | `true`               | `false`                  |
| Region               | `nyc`                               | `nyc`                | `nyc`                    |

Components inside each app:

- **api** (service) — Docker build from `dcw-backend/Containerfile`, `source_dir: dcw-backend`,
  port 8000, health check `GET /health`, receives all ingress traffic.
- **worker** (worker) — same image, `run_command: arq app.domains.ingestion.poller.WorkerSettings`.
  Runs Geotab/Samsara pollers and the compliance sweeper; must be always-on (ARQ cron).

## Deployment flow

Deploys are Git-driven (`deploy_on_push: true`): pushing to an environment's branch
triggers a build + rollout of that environment. Promotion is a merge:

```bash
# dev → staging
git checkout staging && git merge --ff-only dev && git push origin staging

# staging → production
git checkout main && git merge --ff-only staging && git push origin main
```

All commits must be signed (SSH key registered on GitHub as a signing key) and authored
as `mhermoso <8398297+mhermoso@users.noreply.github.com>` — enforced by repo-local git
config (`commit.gpgsign=true`, `gpg.format=ssh`).

## Creating a new environment (staging / prod)

1. **Databases** — create dedicated clusters (never share dev clusters):
   PostgreSQL 16 (`dcw-<env>-pg`) and Valkey (`dcw-<env>-redis`) in `nyc1`.
   For production use `num_nodes: 2` on Postgres for an HA standby.
2. **Branch** — create the environment branch from its upstream (e.g. `staging` from `dev`).
3. **App** — copy the `dcw-dev` app spec (App Platform → Settings → App Spec, or
   `apps-get-info` via the DigitalOcean MCP), rename to `dcw-<env>`, point both
   components at the new branch, and attach the new clusters in the `databases`
   section (`cluster_name`), keeping `DATABASE_URL=${db.DATABASE_URL}` and
   `REDIS_URL=${redis.DATABASE_URL}` bindable references.
4. **Secrets** — set as encrypted env vars (type `SECRET`): `SECRET_KEY` (fresh per
   environment), `GEOTAB_USERNAME`/`GEOTAB_PASSWORD`, `SAMSARA_API_TOKEN` (when Fleet B
   is enabled), `TWILIO_*`. Never reuse the dev `SECRET_KEY` in prod.
5. **Firewall** — after the app exists, restrict each cluster's trusted sources to
   that app only (Databases → Settings → Trusted Sources, or `db-cluster-update-firewall-rules`
   with `type: app`).
6. **Verify** — deployment phase `ACTIVE`, `GET /health` returns ok, worker logs show
   `poll_geotab_feed` / `sweep_active_drivers` cycles, `/ui` renders.

## Production-only hardening checklist

- [ ] Postgres HA (2 nodes) — automatic failover; PITR is included on all plans (7 days)
- [ ] Valkey HA (2 nodes)
- [ ] API `instance_count: 2` for zero-downtime rollouts
- [ ] `ALERT_DRY_RUN=false` only after Twilio numbers are verified for the tenant
- [ ] Custom domain + managed TLS on the app
- [ ] DigitalOcean alerting: deployment failures, CPU/memory thresholds, DB disk usage
- [ ] Branch protection on `main`: require signed commits, disallow force pushes

## Environment variables

| Key | Notes |
| --- | --- |
| `DATABASE_URL` | Bindable `${db.DATABASE_URL}`; app normalizes to `postgresql+asyncpg://…?ssl=require` (`app/core/config.py`) |
| `REDIS_URL` | Bindable `${redis.DATABASE_URL}` (`rediss://` TLS); ARQ parses it via `RedisSettings.from_dsn` |
| `ENVIRONMENT` | `development` / `staging` / `production` |
| `DEBUG` | `false` everywhere except local |
| `SECRET_KEY` | Secret, unique per environment |
| `ALERT_DRY_RUN` | `true` in dev/staging; `false` in prod |
| `GEOTAB_SERVER` / `GEOTAB_DATABASE` / `GEOTAB_USERNAME` / `GEOTAB_PASSWORD` | Geotab feed credentials (username/password are secrets) |
| `SAMSARA_API_TOKEN` | Secret — Samsara Bearer token (Read ELD Compliance; Read Vehicle Statistics for GPS) |
| `SAMSARA_FLEET_ID` | Optional stable fleet/tenant id (`samsara:{org_id}`); derived from `/me` on connect when empty |
| `SAMSARA_API_BASE` | Regional API base (default `https://api.samsara.com`) |
| `SAMSARA_HISTORY_BACKFILL_DAYS` | One-shot HOS lookback on worker startup (default `10`) |
| `SAMSARA_RESCAN_HOURS` | Rolling re-fetch window per HOS poll for late Driver App uploads (default `24`) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_API_KEY_SID` / `TWILIO_API_KEY_SECRET` / `TWILIO_FROM_PHONE_NUMBER` | Telephony (secrets) |
| `DEFAULT_HOME_TERMINAL_TIMEZONE` | e.g. `America/Chicago` |

## Operational notes

- **Append-only guarantee**: `canonical_hos_logs`, `gps_breadcrumbs`, `audit_records`
  are never UPDATEd or DELETEd; schema changes must be additive migrations.
- **Restores**: use the cluster's PITR/fork feature to spin up a copy — never restore
  in place over an environment that has newer audit records.
- **Logs**: App Platform → app → component → Runtime Logs, or `apps-get-logs` via MCP.
- **Rollback**: App Platform keeps previous deployments; use "Revert" on the deployments
  tab (code-only — database state is not rolled back).
