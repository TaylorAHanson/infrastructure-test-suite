# ADR 0017 — Open-SDK authoring, identity-bounded safety

- **Status:** Accepted (2026-08-28). Resolves [#27](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/27). **Amends ADR-0001, ADR-0004, ADR-0006.**
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

ADR-0001/0006 required a Test to name a **handler-manifest operation** and forbade referencing any operation absent from a manifest; ADR-0004 made a per-operation **risk tier R0–R3**, declared in that manifest, the **primary** input to the safety gate. That makes the manifest a hand-authored allowlist — real dev work and tribal knowledge, cutting against the zero-training principle *for maintainers* and capping coverage at whatever's been manifested.

Two reframings in discussion changed the calculus:

1. **CI-gating is not a design driver.** The primary shapes are a **nightly run of "all"** and an **on-demand API for an individual Test or Suite**; a GitHub Action is just one authenticated caller that maps its own pass/fail onto the API (the caller does that work). The run still produces a **verdict** returned by the Run API / poll endpoint (ADR-0005 unchanged) — but "keep a deterministic CI gate stable" stops being the force behind the manifest. In a nightly-triage world, **agentic/advisory results are the normal mode**, feeding the "What's Interesting" view (#10/#23).
2. **The binding blast-radius control is the *identity*, not per-operation risk tiers.** Every Target runs as a **least-privilege per-target service principal** (ADR-0003), and per #26 can run as a **scoped assumed role**. An open SDK call is therefore bounded by *what that identity is actually granted*: a destructive call against a `read-only` target simply fails with a permission error — safe by construction, no manifest required.

## Decision

1. **Open SDK by default.** A Test expresses its operation as an arbitrary **Databricks SDK/API call**, bounded by a **blocklist**, *not* by a curated per-operation manifest allowlist. The Assistant (ADR-0006) grounds on the **live SDK/OpenAPI surface** — more complete than any hand-written manifest — rather than on a hand-authored handler catalog. This **amends ADR-0006's** "cannot reference an operation absent from a manifest": the ground truth is the SDK surface minus the blocklist.
2. **Safety is identity-bounded, in this order of strength** (this **amends ADR-0004**: per-op risk tier is no longer the *primary* gate):
   - **(a) Least-privilege per-target SP / assumed role (ADR-0003, #26)** — the hard bound. The three-layer model's strongest layer becomes primary.
   - **(b) Blocklist** — the global **R3-never floor**; a denylist of the catastrophic, which is all it must catch because (a) and (c) handle the rest.
   - **(c) Coarse verb/category-derived risk tier** — `GET`→low, mutating verbs→higher — checked against the Target's **safety class** as a *secondary* cap.
3. **Light one-confirm risk tier only for mutating targets (option B).** For an operation that runs against a **mutating** (`sandbox` / `prod-for-us`) Target, an **operation-specific risk tier is captured once, suggest-then-human-confirms** (ADR-0006 pattern), because coarse verb-derivation can under-classify a catastrophic mutating call (`create-cluster` and `delete-workspace` are both `POST`). **`read-only` Targets need no manifest and no tier confirmation** — open SDK runs freely, bounded by the read-only identity. Manifest-like effort is thus spent **exactly where blast radius is real, and nowhere else.**
4. **Verdict unchanged; deterministic vs agentic is a caller concern.** A run still yields a `pass`/`fail`/… verdict on the Run API/poll (ADR-0005). Assertions may be `deterministic` or `agentic` (ADR-0008); in the nightly-run-all + on-demand-API world, advisory is normal and feeds the triage view. A caller that wants a hard gate (a wired GitHub Action) selects deterministic Checks/Suites accordingly — **ADR-0008's "agentic never gates" survives as a per-caller rule, not a platform-central gate.**
5. **Manifest demoted, not abolished.** No required per-op allowlist. The ADR-0001 "handler manifest" concept collapses into: **the SDK/OpenAPI surface + the blocklist + the per-mutating-operation tier confirmations from (3)**, plus optional counterfactual/evidence *hints* the Assistant may attach. The **nightly "all"** runs the saved-Test corpus (Lakebase-authoritative, ADR-0001).

## Consequences

- **+** Truest **zero-code authoring** — the original goal. No manifest-authoring bottleneck; coverage isn't capped at what's been hand-declared.
- **+** #26 (assume-role) and #27 are **complementary, not compounding-risk**: the least-privilege / assumed-role identity is precisely what makes open-SDK safe enough, since blast radius is bounded by grants regardless of what's called.
- **+** Manifest effort shrinks to a **one-confirm tier on mutating-target operations only**; read-only validation (the bulk of a ~100-target sweep) needs none.
- **−** Risk-tiering goes **coarse** where it isn't confirmed — a verb can't distinguish a benign `POST` from a catastrophic one — so on mutating targets safety leans on the least-privilege identity + blocklist + the (3) confirmation. Accepted *because* of the identity model; the blocklist must be maintained as the catastrophic floor.
- **−** Grounding shifts from a curated catalog to the SDK/OpenAPI surface: the Assistant can now reach far more, so its validation, R3-refusal, and suggest-then-confirm guardrails (ADR-0006) carry more weight, and hallucinated/wrong calls surface as run errors in triage rather than being prevented by an allowlist.
- **Note:** the no-code GUI form (ADR-0001) becomes **Assistant/OpenAPI-driven** (NL → SDK call + a generic call editor) rather than manifest-form-driven.

## Downstream

Amends **ADR-0001** (handler manifest is no longer the operation gate; SDK surface + blocklist replace the allowlist), **ADR-0004** (identity is the primary blast-radius bound; per-op risk tier becomes a coarse-derived secondary cap + a mutating-target confirmation), and **ADR-0006** (Assistant grounds on the SDK/OpenAPI surface, not a manifest allowlist). Relies on **ADR-0003** (least-privilege per-target SP) and is strengthened by **#26** (assumed role). Leaves **ADR-0005** (verdict) and **ADR-0008** (deterministic/agentic) intact, reframed as caller concerns. Updates CONTEXT.md (amends **Handler manifest**; adds **Open-SDK authoring** + **Identity-bounded safety**). **Spec amendments required:** #19 (core — manifest/risk-tier), #20 (targets/safety — enforcement order), #22 (Assistant — grounding surface); a consolidated spec revision should follow once #26 also resolves, since both touch #20/#22.
