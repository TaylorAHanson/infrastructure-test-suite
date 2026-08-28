# Gating a deploy on validation (the CI contract)

The validation platform exposes **one shared Run API** behind all three triggers
— the CI Action, the manual GUI, and the nightly job (ADR-0005). CI is **just an
authenticated caller**; there is no CI-only path, no idempotency key, and no
synchronous shortcut.

## The contract

| Step | Call | Result |
| --- | --- | --- |
| Start | `POST /api/v1/runs` | `202` + `{ "run_id": "req_…" }` |
| Poll | `GET /api/v1/runs/{run_id}` | `{ "phase": "queued"\|"running"\|"terminal", "verdict": … }` |

- **The caller owns the timeout.** The server never holds the connection open;
  poll `GET` until `phase == "terminal"` (or your own deadline elapses).
- **Fail-closed gate.** Only `verdict == "PASS"` exits 0. `fail`,
  `BLOCKED_BY_SAFETY`, `error`, and a caller-side `timeout` all block. A
  `BLOCKED_BY_SAFETY` in CI means a **misconfigured suite** (it tried an op above
  the target's safety-class cap) — fail loudly.
- **`queued` is invisible.** A run waiting on a busy mutating target
  (`prod-for-us`/`sandbox` serialize; `read-only` never locks — ADR-0011) is
  simply "not terminal yet." Keep polling; the fail-closed mapping is unchanged.
- **Depth defaults to `quick`.** Ask for `"depth": "full"` explicitly if you
  want the functional path (nightly does; CI usually doesn't) — ADR-0007.
- **You resolve the target.** Pass explicit `target_ids` (or `workspace_name`);
  "what changed → which target" is the caller's job, not a server resolver.

## Auth

The CI caller authenticates as a **distinct, trigger + read-verdicts-only**
identity (ADR-0005 §6) — GitHub OIDC → Databricks federated token (preferred, no
long-lived secret) or a service-principal client-credentials secret (fallback).

It can never change a target's safety class and never carries the override. This
is enforced two ways: the platform ships **no app-local authorization** — access
is scoped at the host Databricks workspace, and machine identities stay
least-privilege there (ADR-0010); and even if a machine caller *sends* an
override in its payload, the **safety gate rejects it** — the override is
human-and-GUI-only, so a `CI`/`NIGHTLY`/`API` source can never escalate its own
blast radius (ADR-0004 §5, enforced in `app/services/safety.py`).

## Files

- [`example-github-action.yml`](./example-github-action.yml) — a copy-and-adapt
  workflow snippet. **Not** a maintained first-party Action (ADR-0005 §8): the
  plain-HTTP API is the product, and any IaC/CI system that can make an
  authenticated HTTP call gates the same way.
- The nightly Databricks Job (`databricks/jobs/run_suite.py`) calls this same API
  with `depth=full` — one engine behind all three triggers.
