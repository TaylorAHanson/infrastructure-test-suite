# ADR 0005 — CI invocation contract

- **Status:** Accepted (2026-08-27). Resolves [#7](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/7). Refines [ADR-0002](./0002-target-and-environment-model.md).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1).

## Context

The **primary trigger** is a GitHub Action on an infra deploy that calls the deployed app's API, runs the relevant selection against a target, and **blocks on pass/fail to gate the pipeline** (manual GUI and nightly DAB job are the other two triggers; **one run engine** behind all three). Databricks Apps sit behind infra that kills long-lived connections; runs can take minutes; the tool ships **open source**, so the contract must be self-explanatory and free of tool-specific coupling. Safety (ADR-0004): CI is capped by safety class and **never** gets the override.

## Decision

1. **CI is just an authenticated caller of the shared run API** — same engine and same selection contract as the GUI and the nightly job. There is **no CI-only path**.
2. **Async start → poll → get.** `POST /runs` starts a run and returns a **`run_id`**; the caller polls `GET /runs/{run_id}` until terminal. **The caller (the Action) owns the timeout** — the runner blocks in the workflow, not on a held HTTP connection.
3. **Target selection is explicit and tool-agnostic.** The API takes **explicit target(s)**; the **caller** owns the "what changed → which target(s)" logic (Terraform, CloudFormation, Pulumi, a human — none privileged). Each target is addressed by **workspace name** (primary), or optionally **host** or **workspace_id** — **one of the three required**. An unknown/unregistered target is a **hard error, never a silent pass**. *(This shifts left the CI/Terraform resolution ADR-0002 §6 deferred here, and retires the Target "stack identifier" as an app mechanism.)*
4. **Run selection matches the API exactly:** a **suite** reference *or* an explicit **set of Tests**. No CI-only convention or default suite. One Run may span multiple targets; its verdict aggregates.
5. **Verdict is one shared resource** (`GET /runs/{run_id}`): overall `verdict`, per-Test roll-up, **failure-taxonomy summary**, and the derived connectable/functional view. **Gate mapping is fail-closed:** `pass` → exit 0; `fail` / `BLOCKED_BY_SAFETY` / `error` / `timeout` → non-zero. Any non-`pass` blocks the pipeline. A `BLOCKED_BY_SAFETY` in CI means a **misconfigured suite** (it tried an op above the target's cap) and must fail loudly.
6. **Auth:** the CI caller authenticates via **GitHub OIDC → Databricks federated token** (preferred, no long-lived secret) or an **SP client-credentials secret** (fallback). It is a **distinct identity** from the per-target SPs (ADR-0003), scoped to **trigger runs + read verdicts only** — never safety-class changes, never override.
7. **Concurrency:** concurrent runs against the same target are **allowed** (each independent, own `run_id`; the safety gate already caps mutation). **No idempotency key** — a retried `POST` simply makes another harmless run and the Action tracks the `run_id` it holds.
8. **Distribution:** **no maintained first-party GitHub Action.** Ship an **example workflow snippet**; the plain-HTTP API is fundamentally the product, so non-GitHub callers use it directly.
9. **Self-Service Center consumption:** **no dedicated verdict-fetch contract.** The SSC only needs whether the **deploy (apply + gate) succeeded or failed**, which the pipeline outcome already carries. The verdict resource still serves humans and the "what's interesting" dashboard ([#10](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/10)) — it is simply not part of the CI/SSC contract.

## Consequences

- **+** One engine, one selection contract, one verdict resource across all three triggers — nothing CI-specific to learn or maintain.
- **+** Tool-agnostic: any IaC/CI system that can make an authenticated HTTP call can gate on validation; GitHub is exemplified, not required.
- **+** Fail-closed gating means a gate that cannot confirm a pass never passes; CI can never trip a destructive op (ADR-0004).
- **+** OIDC-preferred auth keeps the OSS story clean — no shared long-lived secret to leak.
- **−** "What changed → which target(s)" is now the **caller's** responsibility; we provide no resolver. Teams wire it in their own pipeline (accepted price of tool-agnosticism).
- **−** No idempotency key means a retry can leave an extra in-flight run; acceptable because concurrent runs are allowed and independent.

## Downstream

Consumes the result model (ADR-0001), target model (ADR-0002), identity model (ADR-0003), and safety model (ADR-0004). Feeds the suite/cost model ([#9](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/9), same run API under a cost dial) and the "what's interesting" dashboard ([#10](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/10), reads the same verdict resource). A per-suite **serialize** flag for unsafe overlap is left as fog, not decided here.
