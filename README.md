# Databricks Regression Validation Platform

A Databricks-native **regression and connectivity validation platform**. It is
**not** a unit-test project. For a given Check it answers two questions,
derived from *where* a failure occurred rather than asserted directly:

1. **Is it connectable?** (resolve + connect + TLS + auth + authorize)
2. **Does it work?** (the operation itself, and its result)

The full vocabulary — Handler, Check, Test, Suite, Depth, Evaluator, and every
other term used below — is defined once, precisely, in
[`CONTEXT.md`](CONTEXT.md). That file is the source of truth; if this README
ever seems to disagree with it, CONTEXT.md wins.

## Architecture

A single **FastAPI** process is the control plane and result store. It serves
the REST API and a built **React/Vite** bundle (Node is used only at build
time). Operational state — Targets, Checks, Tests, Suites, Runs, the audit
log — lives in **Lakebase** (Postgres); analytical rollups are **Unity
Catalog views** queried via **Databricks SQL**. Runs execute **in-app**, with
bounded concurrency — there is no Databricks Job fan-out; the one Databricks
**Job** in the system just triggers the nightly `full` Run through the same
Run API everything else uses. There is no remote-agent subsystem — every Run
executes from inside the app, against the Databricks control/data plane.

```
React (Vite)  ->  build  ->  backend/static
                                  |
CI / GUI / nightly Job -> POST/GET /api/v1/runs (Run API) -> FastAPI control plane
                       |-> Lakebase (targets, checks, tests, suites,
                       |             runs, leases, audit, branding)
                       |-> in-app poller/run engine -> Handlers
                       |      (databricks_sdk, sql_warehouse,
                       |       outbound_http, lakebase)
                       |      -> Databricks resources
                       |-> Databricks Job (nightly trigger only,
                       |      databricks/jobs/run_suite.py, calls same Run API)
                       |-> Assistant (single agent, authoring + triage)
                       |-> completed run w/ findings -> webhook POST
                       |-> UC views + Databricks SQL (analytical rollups)
                       |-> App telemetry -> UC otel_logs/otel_spans/otel_metrics
```

See [`docs/architecture.md`](docs/architecture.md) for detail.

### Key design principles

- **Connectable vs. functional, derived, not asserted**: every Check's result
  maps onto the canonical stage spine `RESOLVE -> CONNECT -> TLS ->
  AUTHENTICATE -> AUTHORIZE -> OPERATE -> VERIFY`; connectable/functional is a
  derived view over the stage reached, not a stored primitive.
- **Deterministic gates, agentic advises**: a Check's assertion is judged by a
  `deterministic` evaluator (structured predicate, same evidence -> same
  verdict — the only kind that can gate CI) or an `agentic` one (a pinned LLM
  judge, always advisory, never a fail-closed gate input).
- **One dial, one Run, one verdict**: `Depth` (`quick`/`full`) is the only
  knob for how far up the stage spine a Run goes. Defaults to `quick`; `full`
  is always an explicit opt-in.
- **Open-SDK authoring, identity-bounded safety**: a Test's operation is any
  Databricks SDK/API call, not a curated manifest entry. The primary blast
  radius bound is the least-privilege per-target service principal; a coarse
  risk tier checked against the target's safety class is the secondary cap; a
  global blocklist is the R3-never floor.
- **Handler = the pluggable unit**: a Handler performs one kind of operation
  against a bound Target. Four ship today —
  [`backend/app/handlers/builtin`](backend/app/handlers/builtin).
- **Durable history**: app logs are ephemeral on Databricks Apps; run history
  and the audit log live in Lakebase.
- **Databricks-native auth**: the injected App service-principal OAuth
  identity (or a locally-configured CLI profile). No PATs for app-to-Databricks
  auth.
- **Orchestrate, don't block**: the web process submits/queries; the in-app
  poller executes Runs with bounded concurrency. CI, the GUI, and the nightly
  Databricks Job are all just callers of the same Run API — none of them
  execute Runs themselves.

## Repository layout

```
backend/            FastAPI app, handlers, services, poller, migrations
  app/core          enums, canonical stage spine + failure taxonomy (data,
                    not enums), open-SDK operation model + blocklist, config
  app/models        SQLModel tables (Check, Test, Suite, Target, RunRequest,
                    CheckRun, StageOutcome, TargetLease, AuditEvent, ...)
  app/handlers      Handler ABC + registry (keyed by handler_key) + builtins
  app/services      run engine, target locking, safety gate, risk tiering,
                     authoring, assertion compiler, agentic evaluator,
                     findings, flaky detection, retention, audit, versioning,
                     the Assistant (graph + tools), webhook, branding, seed
  app/workers       async poller (claims + dispatches Run requests)
  app/api/v1        REST routers
  migrations        Alembic
  run.py            entrypoint (uvicorn, binds 0.0.0.0:$PORT)
src/                React + TypeScript + Vite frontend (builds to backend/static)
shared/             cross-cutting contracts (enums.json mirrors core/enums.py)
databricks/jobs     run_suite.py (the nightly suite-runner Job entrypoint;
                    just another caller of the shared Run API)
infra/sql           Unity Catalog / Databricks SQL analytical views
docs/               architecture, deployment, ADRs, PrivateLink/NCC troubleshooting
tests/              backend tests (registry, safety gate, locking, run
                    lifecycle, authoring, the Assistant, ...)
databricks.yml      Databricks Asset Bundle (Lakebase, App, Job, targets)
app.yaml            direct-deploy app config (bundle config.command supersedes)
dev.sh              local dev (backend + Vite, auto-selecting free ports)
RELEASE_NOTES.md    per-version release notes (rendered under Settings → Release
                    notes; newest must match APP_VERSION)
```

## Local development

```bash
./dev.sh
```

`dev.sh` creates the Python venv, installs deps, and runs the FastAPI backend
and the Vite dev server concurrently (auto-selecting a free port pair per
worktree; see the script for the offset/override knobs).

By default `dev.sh` points the app at a **real Databricks workspace** using
your CLI profile (`DATABRICKS_PROFILE`, default `myenv`) so Checks run for
real, not simulated. Set `RV_LOCAL_DEMO=1` to force offline/simulated mode
instead. Either way, on first boot the app seeds a worked-example library —
a handful of Checks/Tests/Suites covering SQL warehouse access, Unity
Catalog listing, outbound egress, a cluster-policy counterfactual pair, and a
Lakebase self-check — so the catalog and the Assistant have something to
ground on immediately. **It does not seed any Runs**: a fresh boot's "What's
Interesting" view is genuinely empty until you trigger a Run yourself.

Run the backend tests:

```bash
cd backend && ./venv/bin/python -m pip install -r requirements-dev.txt
./venv/bin/python -m pytest
```

Build the production frontend bundle (into `backend/static`):

```bash
npm install && npm run build
```

### Operator guide

The user-facing docs (quickstarts, operations manual, glossary, ADRs) are the
markdown in [`docs/`](docs/) plus [`CONTEXT.md`](CONTEXT.md) as the glossary. The
app's **Guide** tab renders them (`src/pages/Guide.tsx`); they're bundled into the
frontend build, so the guide always matches the code that's deployed and edits
show up live under `dev.sh`.

New pages go in `docs/` and get a sidebar entry in `NAV_SECTIONS` in
[`src/guide/content.ts`](src/guide/content.ts). ADRs are picked up automatically.
`docs/agents/`, `docs/prototypes/` and `docs/research/` aren't part of the guide.
`tests/test_guide_links.py` fails on a link to a doc or heading that doesn't exist.

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
- the **App** with `postgres`, `sql-warehouse`, and `serving-endpoint`
  resources and minimal `user_api_scopes`, running `python backend/run.py`,
- a **suite-runner Job** (nightly cron) that calls the same Run API at
  `depth=full`.

Then create the analytical views (replace the placeholders - see
[`infra/sql/README.md`](infra/sql/README.md)) and enable **App telemetry to
Unity Catalog** in the app's settings.

See [`docs/deployment.md`](docs/deployment.md) for the full flow, including the
**stable Git deployment** caveat and **Lakebase cleanup** semantics.

## PrivateLink / NCC troubleshooting

Network failures are normalized into the failure taxonomy and surfaced in the
UI. See
[`docs/privatelink-ncc-troubleshooting.md`](docs/privatelink-ncc-troubleshooting.md),
which maps each bucket (DNS, NCC, network-policy deny, endpoint-not-established,
TLS handshake, OAuth scope, ...) to a concrete remediation.

## What it validates

Four Handlers ship live — `databricks_sdk` (open-SDK: any Databricks SDK/API
call, bounded by the blocklist), `sql_warehouse`, `outbound_http`, and
`lakebase` (see [`backend/app/handlers/builtin`](backend/app/handlers/builtin)).
The repo ships a worked-example library of Checks, Tests, and Suites built on
those four Handlers — the Tests page and the Assistant are how you grow the
catalog from there; a new Test does not require a new Handler.
