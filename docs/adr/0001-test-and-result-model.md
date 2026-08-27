# ADR 0001 — Test & result model

- **Status:** Accepted (2026-08-27). Resolves [#2](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/2).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

The old prototype hardcoded a fixed six-step stage ladder (`StepKind`), a binary **connectable/functional** verdict, and a **mutate/read** boolean — all baked through the engine, storage, UI, and result payload (see the #4 backend inventory, which flagged this as the deepest refactor). We need a model that is **no-code / plain-English authorable**, **target-agnostic** (one definition applied across ~100 environments), and that **gates dangerous operations by design**.

## Decision

1. **Three-level vocabulary** (see `CONTEXT.md`): **Check** (atomic) · **Test** (target-agnostic capability bundle *with counterfactuals*) · **Suite** (run selection).
2. **Tests are target-agnostic**; the **Target is bound at run time**. No target-specific test definitions.
3. Every Check result **maps onto a shared canonical stage spine** (`RESOLVE→CONNECT→TLS→AUTHENTICATE→AUTHORIZE→OPERATE→VERIFY`). Handlers map onto it and skip absent stages; they **do not invent** stages.
4. **Connectable vs functional is a derived view** over the stage reached, never a stored primitive. **Failure classification** is an **extensible taxonomy** over the stages.
5. A Check's **expected outcome** supports **counterfactuals** (expected denial at a named stage; pass = correctly blocked).
6. **Depth rides on the stages** (run up to a threshold). Only a coarse *quick vs. full* dial is required here; the cost/latency strategy is #8.
7. **Assertions are authored in plain text.** The AI assistant **compiles them to deterministic structured assertions** where possible; **deterministic evaluation is the CI default.** Whether/how to support **agentic (LLM-judged)** assertions is deferred to a prototype ([#11](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/11)).
8. **Storage: Lakebase is the source of truth** — Databricks Apps have no stable filesystem, so the app cannot own YAML files. **Rollback is in-app** (Lakebase); **YAML is an import/export/portability format only**, not the system of record.
9. **Operations carry a risk tier** (R0 read / R1 safe mutation / R2 destructive / R3 catastrophic), **declared in the handler manifest**. The enforcement matrix (class→max-tier; R3 global denylist) is #6.
10. **Every handler ships a self-description manifest** (stages, operations + risk tiers, params schema, structured assertions, evidence schema). It powers the no-code GUI, the AI authoring assistant, and the safety gate.

## Consequences

- **+** Plain-English, no-code authoring; one Test runs anywhere; consistent cross-environment analytics via the shared spine; dangerous ops gated by construction.
- **+** Confirms and directs the #4 rebuild: the stage model becomes metadata, not hardcoded enums.
- **−** Lakebase-authoritative means **versioning/rollback must be built in-app** — no git PR/branch workflow for the tests themselves (YAML export is the portability escape hatch).
- **−** Agentic-assertion determinism is an **open risk**, deferred to the #11 prototype; until then the CI gate is deterministic-only.
- **−** The self-describing **handler manifest is now a hard requirement** for every handler — more upfront work per handler, in exchange for the no-code + AI + safety machinery falling out of it.

## Downstream

Unblocks/feeds: AI authoring assistant (#8), "what's interesting" dashboard (#10, consumes the derived views + taxonomy), and the assertion prototype (#11). Still blocked on the target model (#3): safety enforcement (#6), CI contract (#7), suite/cost model (#9). Risk-tier **enforcement** and **safety-class definitions** are explicitly *not* decided here.
