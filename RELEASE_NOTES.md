# Release notes

What changed in each release of the Validation Platform, newest first. The app
renders this file under **Settings → Release notes**, so keep to the shape
below — the backend parses it (`backend/app/core/release_notes.py`):

- `## vX.Y.Z — YYYY-MM-DD` starts a release; the newest one must match `APP_VERSION`.
- A short paragraph under it is the release summary.
- `### Heading` starts a section; each `- ` bullet under it is one note.
  Inline `**bold**` and `` `code` `` are rendered; nothing else is.

Write each note for someone using the app, not reading the diff: what they can
now do or what behaves differently, in one or two sentences.

## v1.0.0 — 2026-10-02

First stable release. The Validation Platform is a Databricks-native app that
checks whether infrastructure changes broke connectivity or functionality
across many workspaces — run from CI, from the GUI, or nightly — and points you
at what regressed and why.

### Runs and verdicts
- One **Run API** (`POST /runs` to start, `GET /runs/{run_id}` to poll) is shared by CI, the GUI and the nightly Job. CI is just another authenticated caller; there is no CI-only path.
- Every Check result maps onto the stage spine `RESOLVE → CONNECT → TLS → AUTHENTICATE → AUTHORIZE → OPERATE → VERIFY`, so a failure reads as `bucket @ stage` and "connectable vs. functional" falls out of where it stopped.
- Verdicts are fail-closed: only `pass` passes a pipeline. `fail`, `BLOCKED_BY_SAFETY`, `error` and a caller-side timeout all block.
- A single **Depth** dial: `quick` (the connectivity end, and the default) or `full` (through `OPERATE`/`VERIFY`, always an explicit opt-in — the nightly Job uses it).
- Runs execute in-app with bounded concurrency. Runs on a mutating Target queue behind a heartbeat-renewed lock; `read-only` Targets run concurrently.

### Targets and safety
- Targets are registered by hand — a workspace that was never registered can't be touched. Each carries an explicit **safety class** (`read-only`, `prod-for-us`, `sandbox`) that is never guessed from the workspace name.
- Each Target runs as its own least-privilege service principal. A Test can optionally assume one registered role-profile, e.g. to prove an analyst is denied something.
- The safety gate checks every operation's risk tier against the Target's safety class at three layers: the run engine, the outbound client, and the service principal's own permissions. Catastrophic (R3) operations are blocked everywhere.
- Changing a safety class, or overriding the gate for a destructive operation on a `prod-for-us` Target, goes through a GUI-only typed-confirmation ceremony and is audited. CI can never do either.
- The Targets list has tag filter chips for fast filtering and multi-select.

### Authoring and the Assistant
- One app-wide **Assistant** lives in a resizable slide-out shelf on every page and knows what you're looking at.
- "Describe a new test" in plain English: the Assistant drafts the Test, compiles the assertion to a deterministic check where it can, and runs it through the same validator as the form. Nothing is saved until you accept it.
- Tests can call any Databricks SDK/API operation, bounded by a global blocklist and the Target's identity — no pre-built operation list required.
- The Assistant can also search existing Tests, compose Suites and submit Runs (each confirm-gated), and help explain failures. It has no way to edit Targets, admin settings, branding or the webhook.
- Selecting a Test shows its Checks, how it was built and what it risks, and its version history — every edit is a new version, and you can roll back.
- LLM-judged (`agentic`) assertions are supported for checks that can't be made deterministic, but they are always advisory and never gate CI.
- Questions and small talk get a direct answer rather than a draft Test, and weak SDK matches are flagged instead of guessed at.

### Navigation
- Four everyday pages — **What's Interesting**, **Runs**, **Tests** and **Audit Log** — plus a collapsible **Settings** group for Branding, Targets, Schedules, Notifications and Release notes.
- **Tests** is the one place to browse, review and add Tests. Suites are filter chips on that page rather than a page of their own, and a **Reference** tab holds the stage spine, failure taxonomy and handlers.

### Triage and history
- The **What's Interesting** view is a findings feed in two lanes: **Recent deployments** (CI Runs from the last 24 hours) and **Overnight** (drift not tied to a deploy). Everything starts collapsed, and the all-green noise folds into "Everything else".
- Each finding shows its provenance — last green to first bad — keyed to a commit or a non-deploy event.
- Flaky Checks are detected and marked advisory so they can't block a pipeline.
- Run history is kept for a configurable window (default 90 days). The most recent green result for each Target and Check is never pruned, so provenance stays accurate for long-stable Checks.

### Bundled catalog
- Ships with ready-to-run Suites: **Identity & Access**, **Governance / Unity Catalog**, **Security Posture**, **Producer**, **Processing**, **Consumer**, a **Sandbox Functional** smoke test (job create → run → delete, with cleanup), and a read-only, deterministic **CI Gate**.
- A tutorial set of worked examples gives the Assistant something to learn from and supplies the starter prompts a first-time user sees.
- Tests and Suites can be exported as YAML.
- No Runs are seeded: a fresh install's triage view stays empty until you run something.

### Admin, audit and integrations
- An append-only **audit log** records every configuration change — Targets, safety classes, overrides, Test edits, branding and webhook — and is filterable and exportable.
- **Branding** (app name, Assistant name, logo, favicon, accent colour, footer) is editable at runtime and applies within seconds, no redeploy.
- A **run-completion webhook** (Settings → Notifications) sends a generic POST when a finished Run has findings (or on every completion, if you turn that on). Point it at Slack, PagerDuty or any receiver of your own.
- **Release notes**, under Settings.

### Deployment and access
- Deploys as a Databricks Asset Bundle that creates the Lakebase database, the App and the nightly suite-runner Job. App telemetry can be exported to Unity Catalog.
- There are no app-local roles: anyone with access to the App can do everything. Control comes from who can reach the App, the safety gate, the confirmation ceremonies and the audit log.
