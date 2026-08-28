# ADR 0006 — AI authoring assistant contract

- **Status:** Accepted (2026-08-27). Resolves [#8](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/8).
- **Note:** Numbered 0006 — ADR-0005 was concurrently claimed by the CI invocation contract (#7).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

The north-star for this platform is *"define a test in one plain sentence"* (e.g. "Start a cluster under compute-policy P and confirm it's allowed"). The platform **ships open source** and must be usable by a stranger with **zero training** and no tribal YAML knowledge. The test & result model (ADR-0001) established the pieces the assistant sits on top of: plain-text assertions **compiled to deterministic structured checks**, self-describing **handler manifests**, the **Check / Test / Suite** vocabulary, **counterfactuals**, and per-operation **risk tiers**. This ADR defines the assistant that turns a natural-language intent into a valid Test — built on the **Model-Serving endpoint already wired in the DAB**.

## Decision

1. **Interaction: hybrid.** Single-shot compile when the intent is unambiguous; the assistant asks **one focused clarifying question** only when a *required* slot can't be resolved from the manifest/target catalog (missing handler/operation, an unspecified param). No open-ended chat surface.
2. **Grounding: tool-calling** over the app's own structured data — `list_handlers`, `get_manifest(handler)`, `list_targets`, `search_examples`. Grounding is **exact**: the model cannot reference an operation absent from a manifest. No embedding/RAG store; no full-context manifest dump (scales as handlers grow).
3. **Validity is never assumed.** AI output is **never trusted raw** — it passes through the **same deterministic validator the no-code GUI uses** (manifest params schema, assertion structure, risk-tier presence), with a **bounded auto-repair loop** (validation errors fed back once or twice). Schema-constrained generation is preferred to reduce malformed output. The assistant is a **faster front-end to the same schema + validator**, never a privileged path.
4. **Safety at authoring time.** Tests are **target-agnostic**, so there is no per-target compliance check at authoring time. Instead the assistant (a) **refuses to author R3** operations at all (mirrors the global denylist), and (b) **surfaces the draft's max risk tier** in plain language ("contains an R2/destructive delete → will be `BLOCKED_BY_SAFETY` on read-only and prod-for-us targets"). The runtime **safety gate** (ADR-0004) remains the enforcement.
5. **Guided authoring** — the through-line. The assistant proactively surfaces the steps a novice wouldn't know to take. Every one is **suggest-then-human-confirms**, never auto-applied:
   - **Counterfactuals:** the handler manifest **declares per-operation counterfactual pairs** (the operation + its expected denial stage). That declaration is the *trigger*; when present, the assistant **drafts a candidate counterfactual Check** for the human to confirm or edit — heavy lifting done, human steps in.
   - **Suite placement:** after a Test is authored, the assistant suggests **which suite it belongs in** — an existing suite, or a proposed new one — grounded in the real suite catalog. It **never auto-files** a Test into a run selection (cost/blast-radius → #9).
   - **Example promotion:** the assistant suggests promoting a good accepted Test into the worked-example library. We **don't count on users to know to do this**.
6. **Scope: authors and edits Tests/Checks.** NL → validated schema, both **create and edit** ("add a counterfactual", "also check the warehouse variant"). Suite *composition* is not authored here — only the placement suggestion of §5.
7. **Non-compilable assertions are deferred to [#11](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/11).** When a plain-text assertion can't compile to a deterministic structured check, the assistant **flags it and is helpful** — explains why, offers a deterministic rephrasing, or notes it as an agentic-path candidate — but **never silently emits a non-deterministic check**. #8 consumes whatever #11 decides.
8. **Test Studio** is the named authoring surface. Lifecycle: **`Draft`** (AI- or human-produced, unsaved) → **explicit human accept** → **`Active`**; editing an Active Test **creates a new version** (Lakebase-authoritative rollback per ADR-0001), old versions retained. It surfaces **last runs** for each Test and a **derivation panel** — chosen handler + operation, each param's provenance, the grounding manifest/example, the draft's max risk tier, and any suggested counterfactuals/suite with rationale — which is what makes an AI-produced Test auditable by a stranger.
9. **Worked-example library:** a **curated seed set shipped in the repo** (so an open-source cloner has a working assistant on day one), **grown** by curated (suggest-then-confirm) promotion of accepted Tests, stored in Lakebase.
10. **Authoring tolerates non-determinism** because it is human-in-the-loop — every draft is reviewed and explicitly accepted, and nothing the assistant emits auto-runs or gates CI. The determinism constraint (ADR-0001) applies to **CI-time assertion evaluation**, not to the authoring model.

## Consequences

- **+** The north-star is realized: one sentence → a validated draft, plus the counterfactual/suite/promotion steps a stranger wouldn't know to take.
- **+** **No privileged path** — same schema, same validator, same safety gate as manual authoring. Auditable and open-source-legible; the derivation panel makes the "why" inspectable.
- **+** Grounding is exact and scales via tool-calling — no embedding infrastructure, no prompt bloat as handlers multiply.
- **−** **New hard requirement on ADR-0001's handler manifest**: operations may declare an **optional counterfactual pair** (the paired operation + expected denial stage). Thin manifests yield a thin assistant.
- **−** Non-compilable-assertion behavior is only a *flag* until #11 resolves.
- **−** Test Studio adds a versioning/state-management surface — already implied by ADR-0001's in-app rollback, now made explicit.

## Downstream

Feeds the "what's interesting" dashboard (#10 — Test Studio surfaces last runs). **Consumes** the agentic-assertion decision (#11). Suite-placement suggestions touch the suite/cost model (#9). Adds the **counterfactual-pair declaration** back onto ADR-0001's handler manifest schema. Does not decide the platform's own multi-user access model (who may author/accept/promote) — that remains in the map's *Not yet specified*.
