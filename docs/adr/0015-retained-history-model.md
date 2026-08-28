# ADR 0015 — Retained-history model (the minimum the core keeps)

- **Status:** Accepted (2026-08-28). Resolves [#16](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/16).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

Two decided features need the core to remember past runs: the **commit provenance thread** (#10 / ADR-0009 — last-green→first-bad, "requires the run store to retain a per-`(target, check)` last-good verdict + recent history") and **flaky detection** (#11 / ADR-0008 — needs a recent sequence of verdicts per check to spot intermittent flips). Results are **Lakebase-authoritative** (ADR-0001).

The scope line was already drawn while charting: a **long-run reporting / trend-analytics BI surface is out of scope** — anyone can point a dashboard at Lakebase, so the platform ships no bespoke reporting UI, and the core retains only the *minimum* those two features need. This ADR fixes that minimum: the grain, the keying, and the retention posture. The **audit log (#16→ADR-0014)** is a *separate* concern — a separate table for config-plane mutations; runs are not audit events.

## Decision

1. **Two Lakebase tables, the natural relational grain.**
   - **`runs`** (header, one per Run): `run_id`, **trigger** (ci / gui / nightly), **depth** (`quick`/`full`, ADR-0007), target(s), **commit sha or non-deploy event ref**, `started_at` / `ended_at`, overall **verdict** (ADR-0005), phase.
   - **`check_results`** (the atomic retained record — one row per **run × target × check**): `run_id`, `target`, `check_id`, **verdict**, **`bucket @ stage`** failure taxonomy (ADR-0001), **evaluator** (`deterministic` / `agentic`, ADR-0008), **evidence pointer**, `timestamp`. The **commit sha** and **target** are denormalized onto the row so the two consumers query it without a join.
2. **Keyed to serve exactly the two consumers — nothing more.** Primary access index on **`(target, check_id, timestamp desc)`**.
   - **Commit provenance (#10):** last-green→first-bad is **derived by query** over that per-`(target, check_id)` history — **not** a separately materialized "last-good" table. One source of truth; ADR-0009's "retain a last-good verdict" is satisfied as a *query need*, not a maintained column.
   - **Flaky detection (#11):** reads the recent verdict sequence per `(target, check_id)`, scoped to **`deterministic`** results (agentic is advisory, ADR-0008); alternating pass/fail without a config change = the flaky signal.
3. **Retention: a bounded rolling window, configurable, default 90 days** — with **one preserved anchor: the most-recent `green` per `(target, check_id)` is never pruned, even when it falls outside the window.** That single retained anchor keeps commit provenance **honest**: a check green for months and then breaking still diffs against the *right* last-good commit, not the arbitrary edge of the window. This is the **opposite posture** from the audit log's unbounded retention (ADR-0014) — run/check volume is large (~100 targets, nightly + every CI deploy), so history cannot grow forever. Cost remains a **non-goal** (ADR-0007): the window is a single generous knob with a sensible default, not a tuned budget primitive.

## Consequences

- **+** The two features (#10 provenance, #11 flaky) are **correct and cheap**: one index serves both, and deriving last-good by query avoids a materialized store that could drift.
- **+** The **last-green anchor** makes bounded retention safe — it removes the one way a rolling window would silently corrupt provenance (diffing against the window edge instead of the real last-good commit).
- **+** **Scope stays honest**: the core keeps only what #10/#11 need; the out-of-scope BI/reporting surface is served by pointing any dashboard at these Lakebase tables, not by a bespoke UI.
- **−** Retention logic is not a pure time-sweep — the "keep latest green per series" exception is a small amount of extra pruning bookkeeping (the deliberate cost of honest provenance).
- **−** A configurable window is one more setting to document for OSS; mitigated by a working default (90 days) so an un-tuned install behaves well.
- **Note:** evidence payload volume rides on the `check_results` evidence *pointer* + ADR-0001's evidence schema; a distinct shorter lifecycle for heavy evidence blobs is **not** specified here (left as a build detail — the retained *verdict* rows are what #10/#11 read).

## Downstream

Consumes ADR-0001 (Lakebase-authoritative store, evidence schema, `bucket @ stage`), ADR-0005 (verdict), ADR-0007 (depth; cost non-goal), ADR-0008 (deterministic vs agentic — flaky scopes to deterministic), and directly **serves** ADR-0009 (#10 commit provenance) and #11 (flaky detection). Sits **beside** the audit log (ADR-0014) — separate table, same store family, different concern (config mutations vs run history). Updates CONTEXT.md (new **Retained history** term). No new tickets surfaced; the sibling frontier ticket notifications (#18) remains open.
