# ADR 0020 — The Assistant's tool inventory is closed by omission

- **Status:** Accepted (2026-08-31). Part of [#56](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/56); the read/grounding inventory is registered by [#57](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/57).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

Once the turn is a model-driven tool-calling loop (ADR-0019), "what can the Assistant do?" is answered by **which tool schemas exist**, not by prose. Some platform actions must never be reachable from a chat turn no matter what the model decides: registering/editing/deleting a **Target** (infrastructure onboarding is a deliberate, human-only ceremony, ADR-0018), any **`/admin/*`** action (seed, purge-runs, purge-all), and editing **Branding** or **Webhook** config. The question is how to guarantee that.

## Decision

1. **The inventory is a closed allow-list, enforced by omission.** The only tools the model can call are the eight read/grounding tools: `search_sdk_surface`, `search_targets`, `search_roles`, `search_examples`, `run_history`, `commit_provenance_thread`, `failure_archive`, `operation_reference`. There is a single dispatch table; a name not in it returns an error result, never an action.
2. **Excluded actions have no schema at all.** Target register/edit/delete, `/admin/*`, Branding, and Webhook endpoints are absent from the registry — a **design-time absence**, not a runtime permission check. There is nothing to refuse because there is nothing to call.
3. **The boundary is asserted by test.** A test pins the registry to exactly those eight names and asserts no write/admin/branding/webhook name is present — so the boundary can't drift open silently. The check reads the registry, rather than provoking a refusal that would imply the action was reachable.

## Consequences

- **+** The blast radius of a compromised or confused turn is bounded by the schema list, not by the correctness of a guard the model could talk around.
- **+** "Read/ground only" is legible: the whole surface is one enumerable list, and widening it is a visible, reviewable code change.
- **−** Every future mutating capability (Suite composition, Run submission) must be added deliberately, each carrying its own suggest-then-human-confirm gate — the inventory does not grow by default.
- **−** The boundary lives in code, so the test that pins it is load-bearing: it is what keeps the omission honest.

## Downstream

Consumes ADR-0019 (the loop that makes the schema list the surface) and ADR-0018 (Target onboarding as human-only ceremony). Later tickets that add Suite-composition and Run-submission tools amend this inventory explicitly.
