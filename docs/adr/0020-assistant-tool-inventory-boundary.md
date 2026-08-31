# ADR 0020 — The Assistant's tool inventory is closed by omission

- **Status:** Accepted (2026-08-31). Part of [#56](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/56); the read/grounding inventory is registered by [#57](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/57). **Amended (2026-08-31)** when the query-answering tools ([#62](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/62)), Suite-composition tools ([#63](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/63)), and Run submission ([#64](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/64)) were added to the inventory — see Decision §1 and §4.
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

Once the turn is a model-driven tool-calling loop (ADR-0019), "what can the Assistant do?" is answered by **which tool schemas exist**, not by prose. Some platform actions must never be reachable from a chat turn no matter what the model decides: registering/editing/deleting a **Target** (infrastructure onboarding is a deliberate, human-only ceremony, ADR-0018), any **`/admin/*`** action (seed, purge-runs, purge-all), and editing **Branding** or **Webhook** config. The question is how to guarantee that.

## Decision

1. **The inventory is a closed allow-list, enforced by omission.** There is a single dispatch table; a name not in it returns an error result, never an action. The tools in it fall into two groups:
   - **Read/grounding (ten, no side effects):** `search_sdk_surface`, `search_targets`, `search_roles`, `search_examples`, `run_history`, `commit_provenance_thread`, `failure_archive`, `operation_reference`, and — added by the query-answering work (#62) — `search_tests` and `search_suites`. (`run_history` was also generalized to summarize the latest runs when called with no ids.)
   - **Mutating (five, each suggest-then-human-confirms):** `create_suite`, `compose_suite_add_test`, `compose_suite_remove_test`, `compose_suite_reorder`, `compose_suite_toggle_det_only` (#63). Each *proposes* a change and returns `requires_confirmation`; nothing is persisted until an explicit confirm endpoint is called (§4).
   Run submission (#64) is not a registry tool — it flows through its own propose-then-confirm turn path and `/assistant/run` endpoint — but it lives under the same boundary and the same confirm gate.
2. **Excluded actions have no schema at all.** Target register/edit/delete, `/admin/*`, Branding, and Webhook endpoints are absent from the registry — a **design-time absence**, not a runtime permission check. There is nothing to refuse because there is nothing to call.
3. **The boundary is asserted by test.** A test reads the registry and asserts no Target-write, `/admin/*`, Branding, or Webhook name is present — so the boundary can't drift open silently. The assertion is on the *absence* of excluded names, not a frozen count, so adding a deliberately-reviewed tool (a Suite-composition tool, say) doesn't trip it while an accidental write/admin/branding/webhook name would. The check reads the registry, rather than provoking a refusal that would imply the action was reachable.

4. **Mutating tools are added deliberately, each with its own confirm gate.** Widening the inventory beyond read/grounding is a visible code change that must carry a suggest-then-human-confirms gate (ADR-0006): the tool proposes, and a separate confirm endpoint applies. The excluded set in §2 never gains a tool this way — the mutating additions are Suite composition and Run submission only.

## Consequences

- **+** The blast radius of a compromised or confused turn is bounded by the schema list, not by the correctness of a guard the model could talk around.
- **+** The surface is one enumerable list, so what the Assistant can do — and can't — is legible at a glance, and widening it is a visible, reviewable code change.
- **−** Every mutating capability (Suite composition and Run submission, so far) has to be added deliberately, each carrying its own suggest-then-human-confirm gate — the inventory does not grow by default, and the excluded set (Target-write, admin, Branding, Webhook) stays out regardless.
- **−** The boundary lives in code, so the test that pins it is load-bearing: it is what keeps the omission honest.

## Downstream

Consumes ADR-0019 (the loop that makes the schema list the surface) and ADR-0018 (Target onboarding as human-only ceremony). The Suite-composition (#63) and Run-submission (#64) tickets amended this inventory explicitly, as recorded in §1 and §4 above; each mutating tool's confirm gate follows ADR-0006.
