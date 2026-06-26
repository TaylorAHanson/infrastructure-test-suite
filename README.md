# Databricks Regression Validation Platform

A Databricks-native **regression and connectivity validation platform** for AWS
workspaces. It is **not** a unit-test project. For each test case it answers two
questions:

1. **Does it work?** (functional)
2. **Is it connectable?** (auth + DNS + routing + handshake + permission precheck)

It validates Terraform / AWS / Databricks changes - especially network and
private-connectivity changes (PrivateLink, NCC, DNS, allowlists, auth, resource
bindings) - across **Producer**, **Processing**, and **Consumer** test
categories, using metadata-driven, plugin-based probes.

## Architecture

A single **FastAPI** process is the control plane and result store. It serves
the REST API and a built **React/Vite** bundle (Node is used only at build
time). Operational state lives in **Lakebase** (Postgres); analytical rollups
are **Unity Catalog views** queried via **Databricks SQL**; heavy/scheduled work
runs on **Databricks Jobs**. A **remote-agent** protocol lets external vantage
points (Power BI, Tableau, on-prem) report signed results back, because an
in-app run cannot prove outside-in connectivity.

```
React (Vite)  ->  build  ->  backend/static
                                  |
Operator/Schedule -> FastAPI control plane (/api/v1)
                       |-> Lakebase (definitions, runs, steps, audit, schedules)
                       |-> poller/worker -> executors (APP_INLINE, SQL_WAREHOUSE, ...)
                       |-> Databricks Jobs (scheduled / heavy fan-out)
                       |-> UC views + Databricks SQL (charts)
                       |-> App telemetry -> UC otel_logs/otel_spans/otel_metrics
Remote agents (BI / on-prem) -> signed results -> /api/v1/agents/results
```

See [`docs/architecture.md`](docs/architecture.md) for detail.

### Key design principles

- **Two questions, not assertions**: every probe separates `connectable` from
  `functional`, across three modes: `PREFLIGHT`, `SMOKE`, `FULL`.
- **Metadata-driven**: a new test is mostly a definition row + a small executor
  plugin registered under an `executor_key`. See
  [`backend/app/executors`](backend/app/executors).
- **Durable history**: app logs are ephemeral on Databricks Apps; durable run
  history and audit live in Lakebase / Unity Catalog.
- **Databricks-native auth**: OAuth service-principal identity injected by the
  platform. **No PATs** for app-to-Databricks auth.
- **Orchestrate, don't block**: the web process submits/queries; the poller and
  Databricks Jobs do heavy validation.

## Repository layout

```
backend/            FastAPI app, executors, services, poller, migrations
  app/core          enums, config (dual-mode auth), logging, ids
  app/models        SQLModel tables (operational system of record)
  app/executors     Executor ABC + registry + built-in probes + stubs
  app/services      run engine, scheduling, audit, analytics, seed
  app/workers       async poller
  app/api/v1        REST routers
  migrations        Alembic (0001_init)
  run.py            entrypoint (uvicorn, binds 0.0.0.0:$PORT)
src/                React + TypeScript + Vite frontend (builds to backend/static)
shared/             cross-cutting contracts (enums.json, remote-agent protocol)
databricks/jobs     run_suite.py (scheduled/heavy Databricks Job entrypoint)
infra/sql           Unity Catalog / Databricks SQL analytical views
docs/               architecture, deployment, PrivateLink/NCC troubleshooting
tests/              backend tests (registry, executors, run lifecycle)
databricks.yml      Databricks Asset Bundle (Lakebase, App, Job, targets)
app.yaml            direct-deploy app config (bundle config.command supersedes)
dev.sh              local dev (backend :8000 + vite :5173)
```

## Local development

```bash
./dev.sh
# backend  -> http://localhost:8000  (OpenAPI at /docs)
# frontend -> http://localhost:5173  (proxies /api to :8000)
```

`dev.sh` creates the Python venv, installs deps, and runs both processes. With
no Databricks binding the app runs in **demo mode**: SQLite + simulated
executors, seeded with the full test catalog and synthetic history so the
dashboard renders immediately.

Run the backend tests:

```bash
cd backend && ./venv/bin/python -m pip install -r requirements-dev.txt
./venv/bin/python -m pytest
```

Build the production frontend bundle (into `backend/static`):

```bash
npm install && npm run build
```

## Deploy to Databricks (Asset Bundle)

Prerequisites: Databricks CLI `>= 0.283`, an FE-VM **serverless** workspace
(for Lakebase), and `databricks auth login`.

```bash
npm run build                              # build React -> backend/static
databricks bundle validate -t dev
databricks bundle deploy   -t dev          # creates Lakebase, App, Job
databricks bundle run regression-validation -t dev   # open the app
```

The bundle ([`databricks.yml`](databricks.yml)) provisions:

- a **Lakebase** database instance (`prevent_destroy`),
- the **App** with `postgres` + `sql-warehouse` resources and minimal
  `user_api_scopes`, running `python backend/run.py`,
- a **suite-runner Job** (nightly cron) that executes scheduled fan-out.

Then create the analytical views (replace the placeholders - see
[`infra/sql/README.md`](infra/sql/README.md)) and enable **App telemetry to
Unity Catalog** in the app's settings.

See [`docs/deployment.md`](docs/deployment.md) for the full flow, including the
**stable Git deployment** caveat and **Lakebase cleanup** semantics.

## PrivateLink / NCC troubleshooting

Network failures are normalized into a root-cause taxonomy and surfaced in the
UI (PrivateLink-specific failure views) and in
[`docs/privatelink-ncc-troubleshooting.md`](docs/privatelink-ncc-troubleshooting.md),
which maps each bucket (DNS, NCC, network-policy deny, endpoint-not-established,
TLS handshake, OAuth scope, ...) to a concrete remediation.

## What it validates (seeded catalog)

- **Producer**: Fivetran / IICS -> `sales_dev.temp`, Auto Loader smoke, Oracle
  Federation, other inbound.
- **Processing**: all-purpose / job / serverless compute, SQL Warehouse Pro &
  Serverless, Model Serving, Databricks Apps, Lakebase, GitHub, external
  catalog, Genie, Agent (enterprise-only).
- **Consumer**: Power BI, Tableau, Salesforce, TM1, Oracle outbound, N8N, other
  outbound.

Four executors ship live (`sql_warehouse_smoke`, `lakebase_smoke`,
`outbound_http`, `remote_agent`); the rest are seeded as metadata with
capability-gated stubs that SKIP cleanly until a real plugin is registered.
