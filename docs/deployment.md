# Deployment

## Prerequisites

- Databricks CLI `>= 0.283` and `databricks auth login --host <workspace-url>`.
- An FE-VM **serverless** workspace (required for Lakebase).
- Node 18+ and Python 3.11+ locally to build.

## Build the frontend first

The single FastAPI process serves the built React bundle from `backend/static`.
Always build before deploying so the static assets are synced:

```bash
npm install
npm run build        # outputs backend/static (do NOT commit; it's git-ignored)
```

## Deploy with the Asset Bundle (recommended)

```bash
databricks bundle validate -t dev
databricks bundle deploy   -t dev
databricks bundle run regression-validation -t dev
```

The bundle ([`../databricks.yml`](../databricks.yml)) creates:

- `database_instances.rv_db` - the Lakebase instance (`prevent_destroy`). The
  app's service principal is granted `CAN_USE` on the instance and the app's
  `postgres` resource is attached with `CAN_CONNECT_AND_CREATE`.
- `apps.regression-validation` - the app, with `postgres` + `sql-warehouse`
  resources, minimal `user_api_scopes`, and `config.command: [python, backend/run.py]`.

### Resource injection (no hardcoded IDs/secrets)

- Lakebase injects `PGHOST/PGPORT/PGDATABASE/PGUSER`; the password is a rotating
  OAuth token injected per connection. **Do not** put a PG password anywhere.

## Analytical views + telemetry

1. Make the operational tables visible to Unity Catalog (Lakehouse Federation
   to Lakebase, or a scheduled sync) and apply the view SQL in
   [`../infra/sql`](../infra/sql) after substituting the `${...}` placeholders.
2. Enable **Databricks Apps telemetry to Unity Catalog** (Public Preview) in the
   app settings so `otel_logs/otel_spans/otel_metrics` are populated; set
   `OTEL_ENABLED=true` (already defaulted in the bundle).

## Direct deploy (without the bundle)

```bash
databricks apps create regression-validation
databricks sync . /Workspace/Users/<you>/regression-validation \
  --exclude node_modules --exclude src --exclude backend/venv --exclude .git
databricks apps deploy regression-validation \
  --source-code-path /Workspace/Users/<you>/regression-validation
```

Here [`../app.yaml`](../app.yaml) supplies the command/env. Attach the Lakebase,
SQL warehouse, and secret resources in the app UI, then redeploy.

## Operational caveats (must honor)

- **Logs are ephemeral.** App stdout/stderr (and `/logz`) disappear when app
  compute shuts down. Durable history/audit lives in Lakebase/UC by design.
- **Bind 0.0.0.0 and the injected port.** `run.py` reads
  `DATABRICKS_APP_PORT`/`PORT`; never hardcode a port.
- **Stable Git deployment.** If the app is deployed from Git and the deployment
  source changes, Git credentials on the app service principal can be deleted.
  Keep the deployment strategy stable (prefer bundle deploy from a fixed repo).
- **Lakebase ownership/cleanup.** The instance has `prevent_destroy`. Removing
  the app's `postgres` resource or the instance has ownership and cleanup
  semantics - detach carefully and confirm who retains `CAN_MANAGE` before
  re-applying the ACL (the inline `permissions` block is authoritative).
- **Restricted egress can break builds.** If egress is restricted, the app build
  may fail unless package repos and required service domains are allowlisted.
  The platform surfaces `BUILD_EGRESS_BLOCKED` in the troubleshooting UX.

## Scheduling

The in-app poller starts the nightly run on `nightly_cron` (five-field cron,
UTC) and dispatches every run. Each cron slot fires once, even when two app
instances overlap during a deploy, and a slot missed while the app was stopped is
skipped. Manual and CI runs use the same orchestration path (`POST /api/v1/runs`).
