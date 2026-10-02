# Writing tests

A Test is a short Python script. Each check in it is one result in a run, so a
Test that makes three claims about a workspace has three checks. Every Test is
written this way. You write and dry-run tests in the **test editor** (**Tests →
New test**, or **Open in editor** on any test), with the assistant beside the
code.

```python
with check("every SQL warehouse auto-stops"):
    warehouses = w.warehouses.list()
    record("warehouses", [wh.name for wh in warehouses])
    for wh in warehouses:
        assert wh.auto_stop_mins, f"{wh.name} never auto-stops"

with expect_deny("analysts can't read payroll", role="analyst"):
    sql("SELECT * FROM hr.payroll.salaries LIMIT 1")
```

The same script runs against any registered Target. Nothing in it names a
workspace; the run supplies one.

## How a check passes or fails

`with check("…"):` passes when the block runs to the end. It fails when:

- an `assert` is false. This is how you say "this must be true", e.g.
  `assert "payroll" in names`. Add a message after a comma to explain the
  failure, e.g. `assert "payroll" in names, f"found only {names}"`.
- a Databricks call fails. A permission error fails the check at the
  AUTHORIZE stage, an expired or invalid identity at AUTHENTICATE, and any other
  API error at OPERATE.

A failed check doesn't stop the script: the next check still runs. If a check
only calls an API and never asserts anything, it only proves the call works.
Name it for that ("warehouses can be listed"), or add the assert that proves its
name. The bundled health Tests are good models: open any of them in the editor.

The editor warns about a check with no `assert` (or `skip()`), because its
name usually claims more than "the call worked".

A check's name is its identity. Its run history follows the name, so rename a
check only when it now means something different. Names must be unique literal
strings: the editor won't save a check whose name is built at run time, and a
check that runs twice (inside a loop, say) is recorded as an error. To cover many
items, loop inside one check and assert on each.

Options:

- `role="analyst"` runs the block as one of the Target's registered
  role-profiles instead of its own service principal.
- `advisory=True` records the result but never fails a run. Use it for
  judgement calls.
- `description="…"` is shown with the result.

## Proving something is blocked

`with expect_deny("…"):` is a counterfactual: it passes only if something in the
block is refused, and fails if the block succeeds. `at=` says what kind of
refusal you expect:

| `at=` | Passes when |
| --- | --- |
| `"AUTHORIZE"` (default) | a permission is denied |
| `"CONNECT"` | the network refuses: the name doesn't resolve, the connection is refused, or TLS fails |
| `"OPERATE"` | a permission denial or any other API error |

An authentication failure, an outage or a timeout never counts as the denial you
asked for: if the identity's token has expired, or a request times out, the check
errors and says so instead of passing. A timeout is ambiguous because a silent
firewall drop and an outage look the same. If your network drops traffic rather
than refusing it, catch `ConnectionFailed` in a `check` and assert that its
`bucket` is `"UPSTREAM_TIMEOUT"`.

```python
with expect_deny("the public internet is blocked", at="CONNECT"):
    http("https://pypi.org/simple/")
```

## Calling Databricks

| Name | What it is |
| --- | --- |
| `w` | The Databricks SDK `WorkspaceClient`, signed in as the Target's identity. Use keyword arguments: `w.clusters.get(cluster_id="…")`. |
| `w.api.get(path, query=…)` | Raw REST for anything the SDK doesn't cover. Also `post`, `put`, `patch` and `delete`. Paths start with `/api/`. The gate judges a raw call like its SDK equivalent: a POST to an action such as `clusters/permanent-delete` counts as a delete, and a statement POSTed to `/api/2.0/sql/statements` is judged by its SQL. |
| `sql(statement)` | Runs a statement on a SQL warehouse and returns rows as dicts (values are strings). It uses `warehouse_id=` if given, then the Target's `warehouse_id` tag, then a running or serverless warehouse. A statement still running after `timeout` (30 s) is polled until it finishes or `max_wait` (300 s) passes, so a cold warehouse doesn't fail the check. A permission error raises `PermissionDenied` and a missing table `NotFound`. |
| `http(url, method="GET")` | Makes a request from the platform's network to prove an egress path is open. Returns `status_code`, `headers` and `text`. It can't reach loopback, link-local or cloud-metadata addresses; private addresses are fine. |

Results behave like dicts with attribute access, so `cluster.cluster_id` and
`cluster["cluster_id"]` are the same thing. A field the response leaves out
reads as `None` (the SDK omits empty fields), but a near miss for a field that is
there, like `cluster.custer_name`, raises `AttributeError` so a typo can't pass
quietly. Use `.get("field")` for a field that may be missing. Long-running
operations return the finished object.

The editor autocompletes `w.<service>.<method>` and flags methods that don't
exist. The assistant looks methods up for you, and can read the Databricks docs.

## Recording evidence

`record(key, value)` attaches a fact to the current check, e.g.
`record("warehouses", names)`. The value is saved with the result. You'll see it
in the dry-run panel and in the run's details, whether the check passed or
failed. Use it for whatever a reader would need to understand a failure without
re-running the test: the list you searched, the setting you read, the id you
created.

`record` only works inside a check. `log(…)` writes a line to the run log
instead, and `print()` output appears under **Output** in a dry run.

## What the script can see

| Name | Value |
| --- | --- |
| `target` | The Target being tested: `name`, `workspace_name`, `host`, `safety_class`, `tags` and `roles`. |
| `depth` | `"quick"` or `"full"`. |
| `params` | Per-run parameters. Empty unless the run supplies some. |

## Settings and skipping

One script serves every Target, so anything that differs between workspaces (a
catalog name, an expected metastore id, a warehouse) belongs in the Target's
**tags**, read with `setting()`:

```python
CATALOG = setting("canary_catalog", "validation_canary")

with check("the expected metastore is assigned"):
    expected = setting("expected_metastore_id")
    if not expected:
        skip("set an expected_metastore_id tag on the target")
    assert w.metastores.current().metastore_id == expected
```

`setting(key, default)` returns the run parameter `key` if the run supplies
one, else the Target's tag `key`, else `default`. `skip(reason)` ends the
current check as **skipped**. A skipped check neither passes nor fails a run,
and its reason is shown with the result. Use it when a Target isn't configured
for a check, rather than letting the check pass vacuously.

You can catch these exceptions to handle an expected failure yourself:
`ApiError`, `PermissionDenied`, `Unauthenticated`, `NotFound`,
`ConnectionFailed`, `SafetyBlocked` and `DepthSkipped`.

## Safety

Every call a script makes goes through the same safety gate as any run:

- **Blocklisted calls never run.** These include deleting a workspace,
  metastore, catalog or storage credential, `DROP CATALOG` / `DROP SCHEMA`,
  `EXECUTE IMMEDIATE` (build the SQL in Python instead), and reading a secret's
  value (check who can read it with `w.secrets.get_acl` instead). The editor
  refuses to save a script that makes one.
- **SQL is judged by what it does.** Reads (`SELECT`, `SHOW`, `DESCRIBE`, …)
  are reads. `DROP`, `TRUNCATE`, `DELETE`, `VACUUM` and `INSERT OVERWRITE` are
  deletes, and other statements (including `CREATE OR REPLACE`) are writes.
  Several statements in one string are judged by the most dangerous one.
- **The Target's safety class caps what can run.** A `read-only` Target allows
  only reads. `prod-for-us` also allows creates and updates. `sandbox` also
  allows deletes. A check that makes a call above the cap is recorded as
  `BLOCKED_BY_SAFETY`.
- **`quick` depth only reads.** A call that would change something is skipped,
  and its check is recorded as skipped. Use `full` to run it.

Scripts run in a separate process that holds no credentials. It can import the
Python standard library but no third-party packages, can't start processes or
open network connections, and can only write files in its own scratch folder.
Every Databricks or HTTP call is made by the platform on the script's behalf,
which is how the gate sees all of them.

Clean up anything a check creates in a `try`/`finally` block, and give
throwaway resources a recognisable prefix such as `vp-test-`. Cleanup is a
delete, so a test that creates and removes things needs a `sandbox` Target: on
`prod-for-us` the create would run but its cleanup would be blocked.

## Dry runs, saving and activating

- **Dry run** (`⌘↵`) executes the script once against the selected Target and shows
  each check's outcome, the calls it made and what it recorded. Failures are
  marked on their lines. A dry run isn't recorded as a run. At `full` depth on a
  Target that allows changes, its calls are real, and they're written to the
  audit log.
- **Save** (`⌘S`) stores a new version of the test. You can roll back to any
  earlier version from the test's history.
- A new test starts as a **Draft**, which never runs on its own. **Activate** it
  to include it in scheduled and manual runs. A draft that has never run can be
  deleted.

## Working with the assistant

Describe the test you want, or the change you want to make. The assistant looks
up SDK methods and the Databricks docs before using them. It can query the
Target with read-only SQL and API calls to see real data, and dry-run its edits.
It never saves or activates a test; you do.

If you ask the Assistant shelf to write a test, it offers **Open in the test
editor**, which starts a new test with your request already sent to this
assistant.

### Watching it work

The assistant shows its work as it happens. Each lookup, query, edit and dry run
appears in the chat while it runs, in words ("Searching the SDK for …"), then
with what came back and how long it took. A timer counts the whole turn. If the
model reasons before it answers, its reasoning is under **Reasoning** in the
log.

To end a turn early, press **Stop** (the square button that replaces **Send**
while it works). An edit it had already made stays ready to review. When a turn
ends, its log folds into one line, such as "6 steps · 14.2s", that you can
expand.

### Reviewing its edits

The assistant changes only the lines that need to change instead of rewriting
the whole script. Its edit opens as a diff over your code, with each change
highlighted:

- **Keep** or **Undo** on a change applies or drops just that change.
- **Keep all** or **Undo all**, in the bar above the diff, settles every change
  still highlighted. Once you've settled some, they read **Keep the rest** and
  **Undo the rest**. The bar counts the changes left.

**Save** and **Dry run** wait until every change is kept or undone. **Review
diff** on an earlier edit in the chat opens it again.

### Assistant settings

**Settings → Assistant** sets the model behind the Assistant (here and in the
shelf) and how far one turn in the editor may go. Every change is audited.

- **Model**: type to search the workspace's chat endpoints. Foundation models
  (the pay-per-token endpoints serving `system.ai` models) are listed first.
  Leave it empty to use the deployment default. **Test** sends one small
  tool-calling request and tells you whether the model answered, called the
  tool, and showed its reasoning.
- **Reasoning**: Off, Low, Medium or High. Reasoning lets the model think before
  it answers. It's slower and uses more tokens, but is often better on hard
  edits. If a model can't reason while calling tools, the assistant runs
  without it and says so.
- **Limits for one turn**: tool calls, a time limit, output tokens per reply,
  and temperature. At a limit, the assistant stops and tells you what it did and
  what's left.
- **What it may do on the selected target**: whether it may **read live data**
  (read-only SDK calls and SQL, as the Target's identity and through its safety
  gate) and **dry-run its edits** (at `quick` depth, recording nothing).
