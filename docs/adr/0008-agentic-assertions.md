# ADR 0008 — Agentic vs deterministic assertions

- **Status:** Accepted (2026-08-28). Resolves [#11](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/11).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

ADR-0001 established that assertions are authored in **plain text**, compiled to **deterministic** structured Checks where possible, and that **deterministic evaluation is the CI default** — but explicitly deferred to this prototype whether the platform should also support **agentic (LLM-judged)** assertions for the plain-text assertions that *can't* compile cleanly ("the warehouse came up healthy-ish", "the error message is clear and actionable"). ADR-0006 (#8) flags such assertions at authoring time but waits on this decision for what to do with them.

The danger is specific: the CI gate (ADR-0005) is **fail-closed** — only a `pass` Verdict lets the pipeline through, every other terminal state blocks. An LLM judge over the same evidence is **not deterministic**: the same clean deploy can yield `pass` on one run and `fail` on the next. A flaky verdict inside a fail-closed gate **blocks a clean deploy** — a broken gate that erodes trust in the whole platform. Plus latency and cost per verdict.

A throwaway logic prototype ([`docs/prototypes/agentic-assertions.prototype.html`](../prototypes/agentic-assertions.prototype.html)) made this concrete: a fail-closed CI Suite of deterministic + agentic Checks, run ×30 against the *same* clean deploy under three candidate policies. With agentic Checks in the gate, the clean deploy was blocked several times out of 30; deterministic-only was 30/30 stable; agentic-advisory kept the gate 30/30 stable while still producing the fuzzy verdicts as signal.

## Decision

1. **Yes, support agentic assertions — but they NEVER gate CI.** The platform supports two Check **evaluator kinds**: `deterministic` (a structured predicate over evidence — the CI default) and `agentic` (a pinned LLM judge over the Check's structured evidence). Agentic verdicts are **advisory**, never part of a fail-closed pipeline verdict.
2. **The CI Suite is deterministic-only** ("deterministic-only suite mode"). A Suite may be marked `deterministic-only`; the fail-closed Verdict is computed from its deterministic Checks alone. This mode is the **default for anything the CI trigger runs** — CI cannot opt an agentic Check into the gate.
3. **Agentic Checks run in advisory lanes** — nightly `full` Runs and the "what's interesting" triage view (#10) — where a flaky judgement is *signal*, not a blocker. An agentic Check's verdict is recorded on the Run and rolled into the failure-taxonomy summary as advisory, and is never a terminal gate input.
4. **The judge is grounded and pinned.** When an agentic Check runs, it uses a **pinned model** (the DAB Model-Serving endpoint) and must **cite the evidence field(s)** it judged on — evaluated strictly over the Check's structured evidence, never over free re-observation. This keeps an advisory verdict auditable in the derivation panel (ADR-0006).
5. **Authoring already handles the hand-off (ADR-0006 §7).** When a plain-text assertion can't compile to a deterministic predicate, the AI authoring assistant flags it and offers to record it as an **agentic advisory** Check ("this can't gate CI — it'll run advisory"), rather than a dead end. `deterministic` → can gate; `agentic` → advisory only.

## Consequences

- **+** The CI gate stays **rock-solid and fail-closed** — no non-determinism can ever block a clean deploy — while the platform still captures the expressive, fuzzy assertions a real reviewer cares about.
- **+** Gives ADR-0006 a concrete rule for non-compilable assertions instead of a deferral: compile → gates; can't compile → agentic advisory.
- **+** Agentic verdicts become first-class signal for the "what's interesting" view (#10) — exactly where flaky/subjective judgement is useful rather than dangerous.
- **−** Adds an `evaluator` kind + advisory verdict path to the result model (ADR-0001) and the Verdict roll-up (ADR-0005): the roll-up must separate **gating** from **advisory** Check results.
- **−** Requires a pinned-model + evidence-citation contract for the judge; model drift on the pinned endpoint is a maintenance concern (advisory-only, so it never breaks CI — it degrades signal quality).
- **−** Cost/latency of the judge is real but bounded to advisory lanes (nightly / on-demand), never on the CI critical path. Cost accounting remains out of scope (ADR-0007).

## Downstream

Resolves the open risk ADR-0001 flagged and the deferral in ADR-0006 §7. Feeds the "what's interesting" dashboard (#10 — consumes advisory agentic verdicts as signal). Adds `evaluator` (`deterministic` | `agentic`) and the `deterministic-only` Suite flag to the vocabulary (CONTEXT.md). Does not decide model-drift monitoring for the pinned judge endpoint — noted as a build-time concern, not a charting decision.
