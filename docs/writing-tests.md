# Writing tests

A Test is a short Python script. Each check in it is one result in a run, so a
Test that makes three claims about a workspace has three checks. You write and
dry-run tests in the **test editor** (**Tests → New test**, or **Open in
editor** on any test), with the assistant beside the code.

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
- a Databricks call fails. For example, a permission error fails the check at
  the AUTHORIZE stage, and any other API error fails it at OPERATE.

A failed check doesn't stop the script: the next check still runs. If a check
only calls an API and never asserts anything, it only proves the call works.
Name it for that ("warehouses can be listed"), or add the assert that proves its
name.

A check's name is its identity. Its run history follows the name, so rename a
check only when it now means something different. Names must be unique literal
strings.

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
| `"CONNECT"` | the network refuses: DNS, connection or TLS |
| `"OPERATE"` | any API error |

```python
with expect_deny("the public internet is blocked", at="CONNECT"):
    http("https://pypi.org/simple/")
```

## Calling Databricks

| Name | What it is |
| --- | --- |
| `w` | The Databricks SDK `WorkspaceClient`, signed in as the Target's identity. Use keyword arguments: `w.clusters.get(cluster_id="…")`. |
| `w.api.get(path, query=…)` | Raw REST for anything the SDK doesn't cover. Also `post`, `put`, `patch` and `delete`. Paths start with `/api/`. |
| `sql(statement)` | Runs a statement on a SQL warehouse and returns rows as dicts. It uses `warehouse_id=` if given, then the Target's `warehouse_id` tag, then the first running warehouse. |
| `http(url, method="GET")` | Makes a request from the platform's network to prove an egress path is open. Returns `status_code`, `headers` and `text`. |

Results behave like dicts with attribute access, so `cluster.cluster_id` and
`cluster["cluster_id"]` are the same thing. Long-running operations return the
finished object.

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
| `params` | Per-run parameters. Empty unless the run supplies some, so read them with a default: `params.get("catalog", "main")`. |

You can catch these exceptions to handle an expected failure yourself:
`ApiError`, `PermissionDenied`, `NotFound`, `ConnectionFailed`, `SafetyBlocked`
and `DepthSkipped`.

## Safety

Every call a script makes goes through the same safety gate as any run:

- **Blocklisted calls never run.** These include deleting a workspace,
  metastore or catalog. The editor refuses to save a script that makes one.
- **The Target's safety class caps what can run.** A `read-only` Target allows
  only reads. `prod-for-us` also allows creates and updates. `sandbox` also
  allows deletes. A check that makes a call above the cap is recorded as
  `BLOCKED_BY_SAFETY`.
- **`quick` depth only reads.** A call that would change something is skipped,
  and its check is recorded as skipped. Use `full` to run it.

Scripts run in a separate process with no credentials and no direct network
access. Every Databricks or HTTP call is made by the platform on the script's
behalf, which is how the gate sees all of them. Clean up anything a check
creates in a `try`/`finally` block, and give throwaway resources a recognisable
prefix such as `vp-test-`.

## Dry runs, saving and activating

- **Run** (`⌘↵`) executes the script once against the selected Target and shows
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
up SDK methods before using them. It can query the Target with read-only SQL and
API calls to see real data, and dry-run its proposals. Each proposal appears as
a diff over your code. Accept or reject individual changes, then **Apply** or
**Discard** the rest. The assistant never saves or activates a test; you do.
