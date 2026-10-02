# Quickstart: day-to-day operations

This is the operator's manual for a platform that's already running. If you
haven't deployed it yet, start with
[Install, configure & add tests](./getting-started.md).

Most days go like this: check **What's Interesting**, triage whatever is there,
and fix or annotate the cause. The platform is built so that a quiet day means
a quiet screen. You shouldn't need to read 100 green rows.

## 1. Start the day in What's Interesting

**What's Interesting** (the app's home page) shows *findings*, not raw Runs. A
finding is a `(target, check)` series that's currently failing. They're split
into two lanes:

| Lane | What's in it | The question it answers |
| --- | --- | --- |
| **Recent deployments** | Failing series from **CI-triggered** Runs in the last 24 h, newest first. Height-bounded; scroll for older. | "CI emailed me. What's up?" |
| **Overnight** | Drift not attributable to a specific deploy, e.g. a metastore grant changed or a policy was edited. | "What happened while I was away?" |

If the platform can't tell which deploy caused a failure, the finding defaults
to **Overnight**. Series that are green or have recovered are collapsed into the
**Everything else** footer.

Each finding is a collapsed row and nothing expands on its own. Read the row
summaries first, then open the ones that matter.

- **Red dot**: a gating failure. This is what blocks pipelines.
- **Amber dot**: `BLOCKED_BY_SAFETY`. The safety gate stopped an operation. It's
  not a functional failure, but someone asked for something above the Target's
  cap.
- **Purple dot / `flaky · advisory`**: recent deterministic verdicts alternate
  between green and bad. Flaky findings are advisory and never gate.

## 2. Triage a finding

Open a finding to see:

- **`bucket @ stage`**: *what kind* of failure, and *where* on the spine. For
  example `DNS_RESOLUTION_FAILURE @ RESOLVE` or `PERMISSION_DENIED @ AUTHORIZE`.
  **Tests → Reference** lists every bucket.
- **gating / advisory**: whether this Check can fail a Run (`deterministic`) or
  is signal only (`agentic`).
- **The provenance thread**: **last green → first bad**, keyed to a commit sha
  or a non-deploy event. This tells you *when it started*, which usually tells
  you *why*.

### Read the stage first

The stage tells you which team to call:

| Failed at | It's a… | Usually means |
| --- | --- | --- |
| `RESOLVE`, `CONNECT`, `TLS` | **connectivity** problem | DNS, PrivateLink/NCC, firewall, or network-policy changes. See [PrivateLink / NCC troubleshooting](./privatelink-ncc-troubleshooting.md). |
| `AUTHENTICATE` | **connectivity** problem | The Target's SP: secret rotated or expired, wrong `key_client_id`, wrong Host. |
| `AUTHORIZE` | **connectivity** problem | A grant or entitlement changed: UC privileges, group membership, an OAuth scope. |
| `OPERATE`, `VERIFY` | **functional** problem | The call ran but behaved differently: a policy now allows what it should deny, a resource is missing, a result changed. |

For a counterfactual Check the logic flips. It *passes* when the operation is
denied at the expected stage, so a failure means **the control fell open**:
something that should have been blocked wasn't. Treat these as high priority.

### Ask the Assistant

With a finding open, open the **Assistant** shelf (bottom-right) and ask
*"why did this start failing?"* The shelf knows which finding is on screen. It
looks up commit history, prior failures with the same `bucket @ stage`, and Run
history, then suggests a cause and a lane. The answer is advisory only. It never
changes a verdict, and it says when it's unsure.

### Go to the evidence

From **Runs**, open the Run and expand the Check. You'll see every stage it
reached, the outcome and failure bucket at each one, timings, and messages.
Checks blocked before dispatch, like `BLOCKED_BY_SAFETY`, have no stage rows.

## 3. Run things on demand

Use the launcher at the top of **Runs** to re-check a fix, validate a change by
hand, or run a new Test:

| Control | Notes |
| --- | --- |
| **Suite** | Blank runs **every Active Test**. Drafts never run, even when named. |
| **Target** | Blank runs against **every registered Target**. The safety gate still applies per Target, so "All" is safe. |
| **Depth** | **Quick** makes read calls only and is fast: a check that would change something, or that judges a result quick depth didn't fetch, is `SKIPPED` with the reason. **Full** runs everything the Target's safety class allows. Quick is the default everywhere, and Full is always an explicit choice. |

### Run phases and verdicts

| Phase | Meaning |
| --- | --- |
| `queued` | Waiting, usually for a **target lock**. `prod-for-us` and `sandbox` Targets run one Run at a time. `read-only` Targets never lock. |
| `running` | Checks are executing, with bounded parallelism. |
| `terminal` | Done. The verdict is final. |

| Verdict | Meaning | Gate |
| --- | --- | --- |
| `PASS` | Every gating Check met its expected outcome | passes |
| `FAIL` | At least one gating Check didn't | blocks |
| `ERROR` | The run couldn't produce a trustworthy answer | blocks |
| `BLOCKED_BY_SAFETY` | An operation exceeded the Target's safety class | blocks |
| `SKIPPED` | Every gating Check was skipped, so nothing was proven | blocks |

Only `PASS` passes. Advisory (`agentic`) results show up in the Run's failure
summary under **Advisory** but never change the verdict.

### The safety override (R2 on `prod-for-us`)

Sometimes you have to run a destructive (R2) Check against a `prod-for-us`
Target. To do that, pick **one specific** `prod-for-us` Target and a scope that
contains an R2 Check. The launcher shows a warning with **Override & run**.

The override requires a reason and the typed phrase `i understand`. It applies
to that **one Run** on that **one Target**, and it's written to the audit log.
It's GUI-only: CI, the nightly Job, and API callers can never use it, even if
they send one. R3 stays forbidden.

## 4. Manage Targets

### Add or retire a workspace

- **Register**: see [Register your first Target](./getting-started.md#_4-register-your-first-target).
- **Deregister**: use the trash icon on the Target's row. The Target stays
  listed as *(deregistered)* with its history intact, but it's no longer
  selected for Runs.

### Change a safety class

Use the safety-class dropdown on the Target's row:

- **Narrowing** a class (e.g. `sandbox` → `read-only`) applies immediately.
- **Widening** a class (e.g. `prod-for-us` → `sandbox`) is the dangerous edit.
  It opens a confirmation dialog that states exactly what will now be allowed,
  and asks for a reason and the typed phrase `i understand`. The before → after
  change and the reason are audited.

There are no roles or admin group in the app. Any authenticated user can do
this, and the audit log is the control. Access to the app itself is managed in
the host workspace through the App's permissions.

### Rotate a Target's credentials

Write the new OAuth secret over the old one in the secret scope:

```bash
databricks secrets put-secret rv-targets prod-us-east --string-value '<new-secret>'
```

Nothing changes in the app. Tokens are short-lived and minted from the scope, so
the next token picks up the new secret.

## 5. Manage Tests

- **New test** opens the test editor on a starter script. A new Test is a
  **Draft**, listed under *Drafts* on the Tests page, and never runs until
  someone clicks **Activate** (or **Accept → Active** on the Tests page).
- **Open in editor** to change a Test. Each save is a new version. Use
  **Version history → roll back to vN** to undo.
- **Suites** are the filter chips on the Tests page. **New suite** and
  **Edit suite** set a Suite's name, Tests and flags by hand.
- **Promote to examples** adds a good Test to the library the Assistant learns
  from.
- **Tests → Reference** lists the stage spine, every failure bucket, and the
  registered handlers.

`POST /api/v1/admin/seed` brings the bundled catalog up to date, and the app
does the same on boot when `seed_on_startup` is on. On a database an older
release seeded, it retires the bundled Tests and Suites the new release
replaces and adds the new ones. A retired Test is disabled, renamed
*(retired)* and hidden from the Tests list, but kept so old Runs still show
it. Tests you wrote are never touched.

## 6. The nightly regression

The `suite_runner` Databricks Job fires on `nightly_cron` (07:00 UTC by default)
and runs **every Active Test against every Target at `depth=full`**. It shows up
in **Runs** with source `NIGHTLY`, and its findings land in the **Overnight**
lane.

- **The Job fails** (non-zero exit) whenever the verdict isn't `PASS`. A red
  nightly Job run in the Jobs UI means "go look at What's Interesting", or that
  the Job couldn't reach the app.
- **Pause or reschedule** by setting `schedule_pause_status` or `nightly_cron` in
  `databricks.yml` and redeploying. Edits made in the Jobs UI are overwritten on
  the next bundle deploy.
- **Prerequisite**: `validation_app_url` must be set. See
  [Tell the nightly Job where the app is](./getting-started.md#tell-the-nightly-job-where-the-app-is).

## 7. CI gating

Your deploy pipeline calls the same Run API: `POST /api/v1/runs`, then poll
`GET /api/v1/runs/{run_id}` until `phase == "terminal"`. Pass the deploy only if
the verdict is `PASS`. Use the **CI Gate** Suite, which is deterministic-only and
read-only, and pass the specific Target ids your change touched. Its Tests read
the [canary fixtures](./canary-fixtures.md), so provision those in every
workspace CI gates.

The full contract, auth model, and a copy-and-adapt GitHub Actions workflow are
in [Gating a deploy from CI](./ci/README.md).

## 8. Get notified about overnight drift

The **run-completion webhook** is the platform's only push notification. Set it
up under **Settings → Notifications**:

1. Add one or more sink URLs (one per line). Use your own receiver, such as a
   Slack incoming webhook, a PagerDuty relay, or an email bridge.
2. Tick **Enabled**.
3. Optionally tick **Fire on every completion**. By default it only fires when a
   Run produces at least one finding.

Each POST carries `event: "run.completed"`, the `verdict`, a `findings` list
(`check_name`, `workspace_name`, `bucket_at_stage`, `gating`), a one-line
`summary`, the commit/event ref, and a `deep_link` back to the app. Delivery is
best-effort, with a few retries and no queue. The app stays the source of truth.

Set `WEBHOOK_DEEP_LINK_BASE_URL` to the app's URL (see
[Tuning](#tuning-knobs)) so `deep_link` is absolute rather than relative.

## 9. Audit and compliance

**Audit Log** is an append-only record of every *configuration* change:

- Target register / edit / deregister
- Safety-class changes, with before → after; widening changes are flagged
- Safety overrides, with the typed reason
- Test create / edit, as a version pointer
- Branding and webhook edits

Filter by Target, event type, or actor, and export as **CSV** or **JSON**. Runs
aren't in the audit log; their history is in **Runs**. Audit records are kept
forever.

## 10. Branding

**Settings → Branding** sets the app name, the Assistant's display name,
footer text, accent colour, logo, and favicon (PNG or SVG, ≤ 1 MB). Changes
apply to open browsers on their next poll, without a redeploy, and every save
is audited.

## Tuning knobs

These are environment variables on the App. Set them under
`resources.apps.regression-validation.config.env` in `databricks.yml` and
redeploy.

| Variable | Default | What it controls |
| --- | --- | --- |
| `MAX_RUN_CONCURRENCY` | `10` | Max Checks a single Run executes in parallel |
| `POLLER_MAX_CONCURRENT` | `10` | Max Runs the in-app poller dispatches at once |
| `LEASE_TTL_SECONDS` | `90` | How long a target lock survives without a heartbeat |
| `MAX_RUN_DURATION_SECONDS` | `1800` | A Run holding a lock past this is force-released |
| `RETENTION_WINDOW_DAYS` | `90` | Run/Check history window. The latest green per `(target, check)` is always kept. |
| `FINDINGS_RECENT_WINDOW_HOURS` | `24` | Width of the Recent-deployments lane |
| `FLAKY_WINDOW_RUNS` | `10` | How many recent verdicts the flaky detector looks at |
| `WEBHOOK_DEEP_LINK_BASE_URL` | *(empty)* | Base URL for the webhook's `deep_link` |

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| Run sits in `queued` | Another Run holds the lock on a `prod-for-us`/`sandbox` Target. It frees when that Run ends, when its heartbeat lapses (90 s), or at the 30-minute max run duration. |
| `BLOCKED_BY_SAFETY` in CI | The Suite contains an operation above the Target's safety class. That's a misconfigured Suite, so fix the selection rather than the Target. |
| Every Check fails at `AUTHENTICATE` | The identity handle is wrong. Check `scope/key` and `scope/key_client_id`, the app SP's `READ` ACL on the scope, and the Target's Host. |
| Checks fail with `SERVICE_UNSUPPORTED` | The Check names a Handler that isn't registered. **Tests → Reference** lists the valid ones. |
| Health Tests fail with `NOT_FOUND` | The canary objects aren't where the Target's tags say. See [Canary fixtures](./canary-fixtures.md). |
| Posture checks fail with `PERMISSION_DENIED` | They read admin-only settings. Run the posture Suite against a Target whose SP is a workspace admin. |
| The Assistant doesn't respond | Check the Model Serving endpoint (`model_serving_endpoint`) exists and the App has `CAN_QUERY` on it. The bundle grants both. |
| Nightly Job fails instantly | `validation_app_url` isn't set. Redeploy with `--var="validation_app_url=…"`. |
| App logs disappeared | App logs are ephemeral on Databricks Apps. Durable history is in Lakebase. Enable App telemetry to UC for longer-lived logs (see [Deployment](./deployment.md)). |
