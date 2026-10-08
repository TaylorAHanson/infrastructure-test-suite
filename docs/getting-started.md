# Quickstart: install, configure & add tests

This guide takes you from an empty workspace to a deployed platform that
validates its first Target with a Test you wrote. Plan on about an hour, most of
it spent creating service principals and the canary objects.

::: tip Vocabulary
The platform uses a small, precise vocabulary: **Target**, **Check**, **Test**,
**Suite**, **Run**, **safety class**, **Depth**. Each term is defined once in the
[glossary](./glossary.md). If a word below seems to mean something specific, it
does.
:::

## What you're setting up

The platform is a single **Databricks App**. One FastAPI process serves the API
and the web UI, keeps its state in a **Lakebase** (Postgres) database, and runs
validation **in-app** against the workspaces you register. Deploying the bundle
creates three things:

| Resource | What it's for |
| --- | --- |
| Lakebase database instance | Targets, the Test catalog, Run history, the audit log, branding. Survives redeploys (`prevent_destroy`). |
| The App | UI + Run API + the in-app run engine, which also starts the nightly `depth=full` run. Runs as its own service principal. |

You then register each workspace you want to validate as a **Target** and give
it its own **least-privilege service principal**. The platform never touches a
workspace you haven't registered.

## 1. Prerequisites

- **Databricks CLI ≥ 0.283**, authenticated:
  `databricks auth login --host https://<your-workspace>`.
- A **serverless** workspace to host the app (Lakebase requires one; an FE-VM
  serverless workspace works).
- A **SQL warehouse** in that workspace. The bundle looks up
  *Serverless Starter Warehouse* by default.
- A **Model Serving endpoint** for the Assistant. The default is
  `databricks-claude-sonnet-5`; you can pick another in **Settings → Assistant**.
- To build or develop locally: **Node 18+** and **Python 3.11+**.

## 2. (Optional) Try it locally first

Running locally is the fastest way to look around before you deploy:

```bash
./dev.sh
```

`dev.sh` creates the Python venv, installs dependencies, and starts the backend
and the Vite dev server. Ports are picked automatically and printed on start,
usually backend **8010** and UI **5183**. It also starts this guide at
`/guide/`.

By default the local app runs Checks **for real** against a workspace, using
your CLI profile. Point it at your own profile and warehouse:

```bash
DATABRICKS_PROFILE=<your-profile> DATABRICKS_WAREHOUSE_ID=<warehouse-id> ./dev.sh
```

Or run fully offline with simulated results:

```bash
RV_LOCAL_DEMO=1 ./dev.sh
```

Locally the app uses a SQLite file instead of Lakebase and doesn't seed on boot.
Seed the bundled Test catalog with `curl -X POST localhost:8010/api/v1/admin/seed`
(run it again after pulling a release with new Tests). It creates **no Runs**, so
"What's Interesting" stays empty until you start one.

## 3. Deploy to Databricks

### Point the bundle at your workspace

[`databricks.yml`](../databricks.yml) ships one target, `dev`, whose
`workspace.host` points at the original author's workspace. Change it to your
own, or add a new target next to it:

```yaml
targets:
  dev:
    mode: production
    workspace:
      host: "https://<your-workspace>.cloud.databricks.com"
    variables:
      app_name: "regression-validation-dev"
      environment: "dev"
```

The bundle variables you're most likely to change:

| Variable | Default | Meaning |
| --- | --- | --- |
| `sql_warehouse_id` | lookup: *Serverless Starter Warehouse* | A warehouse in the app's workspace that the App is granted `CAN_USE` on |
| `model_serving_endpoint` | `databricks-claude-sonnet-5` | The Assistant's default endpoint. **Settings → Assistant** can choose another. |
| `seed_on_startup` | `true` | Seed the bundled Test catalog on boot. On a database an older release seeded, it retires the superseded Tests and adds the new ones. |
| `nightly_cron` | `0 7 * * *` (07:00 UTC) | Five-field cron in UTC for the nightly `full` Run; empty turns it off |
| `schedule_pause_status` | `UNPAUSED` | `PAUSED` to deploy without the nightly run |

### Deploy

```bash
databricks bundle validate -t dev
databricks bundle deploy   -t dev
databricks bundle run regression-validation -t dev   # starts the app
```

You don't need to build the frontend yourself. The bundle syncs the source, and
the Databricks Apps Node buildpack runs `npm install && npm run build` at deploy
time. That build produces both the UI and this guide. Running `npm run build`
locally first is harmless and catches build errors earlier.

### Optional: analytics views and telemetry

Dashboards and long-term reporting work through Unity Catalog views over the
operational tables, plus App telemetry to UC. Neither is required to use the
platform. See [Deployment → Analytical views + telemetry](./deployment.md#analytical-views-telemetry).

## 4. Register your first Target

A **Target** is one workspace under test. Every Target has its own OAuth M2M
**service principal** (SP). That SP is the main limit on what a Run can do to the
workspace, so give it only the grants your Tests need, and **never
workspace-admin**.

### Create the target's service principal

In the workspace you want to validate:

1. Create a service principal and generate an **OAuth secret** for it.
2. Grant it only what your Tests need. The bundled health Tests need
   `CAN_USE` on a SQL warehouse and the grants on the canary objects you create
   in [step 5](#_5-provision-the-canary-fixtures). The compute Tests also need
   permission to submit jobs.

::: info The posture Suite needs an admin
The **Security posture (drift)** Suite reads workspace settings, tokens and
groups, which only a workspace admin can see. Run as the least-privilege SP, its
checks fail with `PERMISSION_DENIED`. To run it, register a second
`read-only` Target for the same workspace with an admin SP, and run only that
Suite against it.
:::

### Store its credentials in a secret scope

The app reads Target credentials from a Databricks **secret scope** in the
**app's** workspace. A Target references them by an **identity handle** of the
form `scope/key`:

- the client **secret** lives at `scope/key`
- the client **id** lives beside it, at `scope/key_client_id`

```bash
databricks secrets create-scope rv-targets
databricks secrets put-secret rv-targets prod-us-east           --string-value '<oauth-secret>'
databricks secrets put-secret rv-targets prod-us-east_client_id --string-value '<sp-application-id>'

# Let the app's own service principal read the scope.
databricks apps get regression-validation-dev   # note service_principal_client_id
databricks secrets put-acl rv-targets <service_principal_client_id> READ
```

The platform never stores the secret. It mints short-lived tokens on demand, so
you rotate the secret in the scope and the app needs no changes.

### Register it in the app

Open **Settings → Targets** and fill in **Register a target**:

| Field | What to enter |
| --- | --- |
| **Name** | A stable display name, e.g. `prod-us-east`. The Run API addresses Targets by id; `GET /api/v1/targets` maps names to ids. |
| **Workspace name** | Optional human label. |
| **Host** | The workspace URL, e.g. `https://….cloud.databricks.com`. Tokens are minted against this host. If you leave it blank, the app uses its own workspace. |
| **Safety class** | Start with `read-only`. See below. |
| **Identity handle** | `rv-targets/prod-us-east` |
| **Assumable roles** | Optional. See below. |

**The safety class** sets the most dangerous operation the platform will run
against this Target. It's an explicit field, never inferred from the workspace's
name:

| Safety class | Highest risk tier allowed | Example |
| --- | --- | --- |
| `read-only` | **R0**: reads only | List catalogs, check a policy |
| `prod-for-us` | **R1**: safe mutations | Create a cluster or a job |
| `sandbox` | **R2**: destructive | Delete a cluster, job, or schema |

**R3** (catastrophic, e.g. delete a workspace) is forbidden on every Target. A
`read-only` Target also never locks, so concurrent Runs against it are fine.
Changing a class to a *more* permissive one requires a typed confirmation and is
audited (see [Operations → Targets](./operations.md#change-a-safety-class)).

**Assumable roles** let a Test run as a Databricks **group** instead of the
SP. This is how you test the *denied* side, e.g. "the analyst group can't create
a cluster". Each role has its own safety class, which can't exceed the
Target's. Leave the list empty to start.

**Tags** (`key=value`) appear as filter chips on the Targets page, and they're
also where a Target's specifics live: the bundled Tests read their canary
catalog, warehouse and expected metastore from tags (next step). The
registration form doesn't set them yet. Send `tags` on `POST /api/v1/targets`,
or update an existing Target:

```bash
curl -X PATCH "$APP_URL/api/v1/targets/<target-id>" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"tags": {"canary_catalog": "validation_canary", "warehouse_id": "<warehouse-id>"}}'
```

`PATCH` replaces the whole tag set, so send every tag each time.

## 5. Provision the canary fixtures

The bundled health Tests don't stop at "did the API answer?". They read a known
row, read a known file, write and read back, and try a table they must be
refused. Those objects have to exist in each workspace: a canary catalog and
schema, a one-row table, an ungranted table, an external volume holding one
file, and a scratch schema.

Create them with the Terraform module in `infra/canary/` (alongside the
configuration that manages the workspace) or the one-off script
`infra/canary/provision_canary.py`. Both print the target tags to set. See
[Canary fixtures](./canary-fixtures.md) for what gets created, the grants, and
every tag.

## 6. Run the bundled catalog

The app seeds this catalog on boot. Every Test in it is a Python script you can
open in the editor:

| Suite | Tests | When to run it |
| --- | --- | --- |
| **CI Gate** | Workspace access, Unity Catalog data path | After every apply. Read-only, about a minute, any safety class. |
| **Post-apply health: compute & network** | Classic compute, Serverless compute, SQL warehouse cold start, Unity Catalog write round-trip | At **full** depth on a `prod-for-us` or `sandbox` Target, after applies that touch networking, and nightly. 5–15 minutes. |
| **Security posture (drift)** | Exfiltration controls, Token hygiene, Network access controls, User isolation, Admin footprint | On a schedule, as an admin (see above). It's configuration drift, not health, so it isn't part of the CI Gate. |
| **Connectivity Suite** | SQL Warehouse validation, Unity Catalog access, Egress connectivity | Short, read-only scripts that show `sql()`, the SDK and `http()` at work. A good first run, and a good start for your own Tests. |

To run it, open **Runs** and use the launcher at the top:

1. **Suite**: pick **CI Gate**. **All active tests** runs every Active Test.
2. **Target**: pick the Target you just registered. **All targets** is also
   safe, because the safety gate runs per Target. There's no default, so
   **Run** stays disabled until you choose.
3. **Depth**: leave it on **Quick**.
4. Click **Run**. You land on the Run page, which updates as Checks finish.

To run a single Active Test, select it on the **Tests** page and use **Run this
test** in its detail pane: pick a Target and a depth, then click **Run**.

A Run moves through `queued` → `running` → `terminal` and ends with one verdict:
`PASS`, `FAIL`, `ERROR`, `BLOCKED_BY_SAFETY`, or `SKIPPED` (nothing gating
ran). Every skipped check says why: a quick run never makes changes, and a part
of a test whose tag isn't set (e.g. `classic_warehouse_id`) is skipped. Expand
any Check to see each stage it reached on the spine
`RESOLVE → CONNECT → TLS → AUTHENTICATE → AUTHORIZE → OPERATE → VERIFY`.

::: tip If the health Tests fail with NOT_FOUND
The canary objects aren't where the tags say. Check `canary_catalog` and
`canary_schema` on the Target, and that the SP has `USE CATALOG` on the catalog.
:::

::: warning If everything fails at AUTHENTICATE
The identity handle didn't resolve to a working SP. Check that both secrets
exist (`key` **and** `key_client_id`), that the app's SP has `READ` on the scope,
and that the Target's **Host** is right.
:::

## 7. Add your own test

A Test is a short Python script. Each `with check("…"):` block in it is one
result in a run, and it passes when the block finishes without a failed
`assert` or API error:

```python
with check("every SQL warehouse auto-stops"):
    warehouses = w.warehouses.list()
    for wh in warehouses:
        assert wh.auto_stop_mins, f"{wh.name} never auto-stops"
```

1. Open **Tests** and click **New test**. The **test editor** opens with a
   starter script on the left and the Assistant on the right.
2. Write the script, or describe what you want to the Assistant, e.g.
   *"check that every SQL warehouse auto-stops"*. It looks up the SDK methods,
   can query the Target read-only to see real data, and shows each step as it
   works. Its edits appear as a diff you keep or undo, change by change. It
   never saves anything.
3. Pick a Target and press **Dry run** (`⌘↵`) to run the buffer once. You see each
   check's outcome, the calls it made and what it recorded. A dry run isn't
   recorded as a run.
4. Click **Create draft**. A Draft never runs on its own.
5. Click **Activate**. The Test now runs in manual and scheduled runs.

[Writing tests](./writing-tests.md) is the full reference: the helpers (`w`,
`sql()`, `http()`, `record()`, `setting()`, `skip()`), how checks fail, and
what the safety gate allows at each depth.

### Gating and advisory checks

A check fails a Run only through its own `assert`s and API errors, so the same
evidence always gives the same verdict. Mark a judgement call
`check("…", advisory=True)`: you still see the result, but it never fails a
Run. The CI Gate admits only read-only Tests with no advisory checks.

A check with no `assert` passes whenever its calls succeed. The editor warns
about one, because its name usually claims more than that.

### Counterfactuals: test that bad things are blocked

`with expect_deny("…"):` passes only when something in the block is refused,
and fails if it succeeds:

```python
with expect_deny("the ungranted table is refused"):
    sql("SELECT * FROM validation_canary.health.forbidden")
```

It counts only the refusal you asked for: a permission denial by default, a
network block with `at="CONNECT"`. A broken identity or an outage never
counts. A control that's never tested on its deny path can silently fall open,
so this is usually the most valuable check in a Test.

### Suites and editing

- **Suites** are filter chips on the Tests page. **New suite** names one and
  picks its Tests; **Edit suite** changes it. A Suite CI runs should hold only
  read-only Tests with no advisory checks, like the CI Gate.
- **Change a Test**: **Open in editor**, edit, **Save**. Each save is a new
  version, and earlier versions stay in **Version history**, each with
  **roll back**. Rolling back asks you to confirm, then saves the old script as
  a new version.
- **Tune the Assistant**: **Settings → Assistant** picks its model and limits
  what one turn may do. See
  [Writing tests → Assistant settings](./writing-tests.md#assistant-settings).

## Next steps

- Wire the **CI Gate** Suite into your deploy pipeline: see
  [Gating a deploy from CI](./ci/README.md).
- Point the **run-completion webhook** at Slack or PagerDuty so overnight drift
  reaches you: see [Operations → Notifications](./operations.md#_8-get-notified-about-overnight-drift).
- Read the [day-to-day operations manual](./operations.md).
