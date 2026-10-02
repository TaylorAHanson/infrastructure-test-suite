# Databricks Regression Validation Platform

**Know your Databricks workspaces still work after every terraform apply, every
night, and before every release.**

Platform changes break things quietly: a network rule, a grant, a cluster policy,
a PrivateLink endpoint. You usually find out when a customer's job fails. This
app runs short Python tests against every workspace you register, and tells you
whether something broke and exactly where.

## Tests are just Python

```python
with check("every SQL warehouse auto-stops"):
    for wh in w.warehouses.list():
        assert wh.auto_stop_mins, f"{wh.name} never auto-stops"

with expect_deny("analysts can't read payroll", role="analyst"):
    sql("SELECT * FROM hr.payroll.salaries LIMIT 1")
```

Each `with check(...)` block is one result. Inside it you use the Databricks SDK
(`w`), SQL, and HTTP, and `assert` what must be true. `expect_deny` proves a
control holds by passing only when the call is refused. The same script runs
against any workspace, because nothing in it names one.

## Why teams use it

- **It shows where things broke.** Every failure is placed on one path: DNS →
  connect → TLS → authenticate → authorize → operate → verify. So "the network
  blocks it" never looks like "the permission is missing". Network failures
  map to a fix in the [troubleshooting guide](docs/privatelink-ncc-troubleshooting.md).
- **An assistant writes the tests with you.** Describe what you want to prove.
  The assistant looks up the SDK, reads live from the workspace, edits the
  script, and dry-runs it while you watch. You keep or undo each change.
- **It's safe to point at production.** Each workspace has a safety class
  (read-only, sandbox, or prod-for-us). Each test gets a risk tier from the
  calls it makes. A test can't do more than its workspace allows, quick runs
  skip anything that changes state, and catastrophic calls are blocked
  everywhere. Every change and override is audited.
- **Useful from the first run.** It ships with 14 tests in 5 suites: connectivity,
  compute and network health, security-posture drift, a CI Gate for after every
  apply, and a full regression.
- **It runs wherever you need it.** Run tests from the UI, from CI through the
  Run API ([GitHub Action example](docs/ci/example-github-action.yml)), on a
  schedule, or nightly. Results that need attention go to a webhook. *What's
  Interesting* flags failures, flaky checks, and the last good run before each
  first failure.
- **It's Databricks-native.** It runs as a Databricks App on Lakebase, deploys as an
  Asset Bundle, and authenticates as the App's service principal. No personal
  tokens.

## Try it

```bash
./dev.sh                     # against your Databricks CLI profile (default: myenv)
RV_LOCAL_DEMO=1 ./dev.sh     # offline demo, no workspace needed
```

Deploy to a workspace:

```bash
npm install && npm run build
databricks bundle deploy -t dev
databricks bundle run regression-validation -t dev
```

## Learn more

- [Getting started](docs/getting-started.md): deploy, register a workspace,
  and run the bundled tests
- [Writing tests](docs/writing-tests.md): the script API, the editor, and the
  assistant
- [Operations](docs/operations.md): triage, CI gating, nightly runs,
  notifications, and audit
- [Deployment](docs/deployment.md) and [architecture](docs/architecture.md)
- [Glossary](CONTEXT.md): what Test, Check, Suite, Target, and Depth mean

The same docs are in the app's **Guide** tab.
