# ADR 0002 — Target & environment model

- **Status:** Accepted (2026-08-27). Resolves [#3](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/3).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1).

## Context

Tests are target-agnostic (ADR-0001), bound to a target at run time; operations carry risk tiers gated by a target's safety class. We validate across **~100 Databricks workspaces** that change constantly, **some of which must stay untouched** for security/compliance. The tool ships **open source**, so the model must be self-explanatory and free of internal-only dependencies.

## Decision

1. **Target = one Databricks workspace** (host + workspace id). Finer scope (catalog/warehouse/policy) is a **Check param**, not a separate target — otherwise the registry explodes.
2. **Manual, opt-in registration** (GUI CRUD, stored in Lakebase). **No auto-discovery.** A workspace that was never registered can never be touched — the safe default for compliance-isolated workspaces, and no dependency on account APIs an OSS user may lack.
3. **Safety class** is an **explicit, authoritative** field — **never derived** from the workspace name (the label lies: a "dev" workspace can be prod-for-us). Enum `read-only / prod-for-us / sandbox`. **Changing it is a high-friction, audited action.** Class caps the max risk tier (ADR-0001); rare **audited per-target exceptions** allowed, default none.
4. **Environment (dev/stage/prod) is a descriptive tag, not an entity** — the label is unreliable; safety class is the authoritative control. (Almost everything is prod to the platform team.)
5. **Tags** are optional `key=value` labels used only as **filter chips** + one-click multi-select — **not** an authored query language.
6. **Selection is always explicit — no implicit default:**
   - **GUI:** a picker with **"All"** at the top + individual targets, filterable by tag chips.
   - **API:** named target(s), or explicit `"all"`.
   - **CI/Terraform:** the TF apply knows **which stack** is changing → the Action passes the **affected target(s)** automatically. Scoped runs, zero human input. A target carries an optional **stack identifier** to make stack→target resolution trivial (resolution logic is #7).
   - **Safe by construction:** even "All" can't harm a protected workspace — the per-target safety gate fires per target.
7. A target carries an **identity handle** (which service principal); secret storage/assumption mechanics are #5.

## Consequences

- **+** Dead-simple, no-training selection; nothing to maintain in the common case; scoped CI runs save time; compliance-isolated workspaces are untouchable by default; fits open source (no internal-only discovery).
- **−** Humans must add/remove the ~100 targets manually — accepted as the price of opt-in safety.
- **−** Needs a **target↔stack convention** for CI auto-targeting to shine (deferred to #7).

## Downstream

Unblocks #5 (credential mechanics behind the identity handle), #6 (safety-class enforcement matrix), #7 (CI contract + stack→target resolution), #9 (selection feeds the cost model).
