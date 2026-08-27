# CONTEXT — Databricks Validation Platform

Ubiquitous language for the platform. Use these terms **exactly** in code, issues, tests, and UI — don't drift to synonyms. New concepts get added here as they're decided (see `docs/adr/`).

> Status: seeded while resolving the test & result model ([#2](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/2), ADR-0001). Target and safety details are finalised in [#3](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/3) / [#6](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/6).

## Core vocabulary

- **Validation Platform** — this app. A Databricks-native platform that regression- and connectivity-validates infra changes across many environments.
- **Handler** — a plugin that knows how to perform operations of a given kind against a Databricks target (the rebuilt "executor"). Ships a **handler manifest**.
- **Check** — the **atomic** unit of validation: one handler performing one operation against a bound target, producing a **staged result** + **evidence**. Carries a plain-text **assertion** and an **expected outcome**.
- **Test** — a **user-facing, target-agnostic** capability assertion, composed of an ordered set of Checks **including counterfactuals**. Passes iff every Check meets its expected outcome. *Defined once, applied to any target.*
- **Counterfactual** — a Check whose **expected outcome is a denial/failure** (pass = it was correctly blocked at the expected stage). E.g. "a non-compliant cluster is denied by policy P."
- **Suite** — a selection of **Tests + targets** chosen for a run; the cost/scheduling grouping.
- **Target** — one Databricks **workspace** under test (~100 of them). Target-agnostic Tests are **bound to a Target at run time**. **Registered manually / opt-in** (a never-registered workspace can never be touched). Carries a **safety class**, an **identity handle** (its OAuth M2M service principal, referenced as a Databricks secret-scope `scope/key` — ADR-0003), optional **tags**, and an optional **stack identifier** (so a Terraform change can resolve to the target it affects). Finer scope (catalog/warehouse/policy) lives in **Check params**, not in separate targets.
- **Run** — one execution of a Suite (or Test) against selected Targets.
- **Canonical stage spine** — the shared, ordered vocabulary every Check's result maps onto: `RESOLVE → CONNECT → TLS → AUTHENTICATE → AUTHORIZE → OPERATE → VERIFY`. Handlers **skip** stages they lack; they **do not invent** private stages.
- **Failure classification** — an **extensible taxonomy** over the stages: the category of a failure.
- **Connectable / Functional** — a **DERIVED VIEW** over the stage reached, **not** a stored primitive. Failure ≤ `AUTHORIZE` = connectivity; failure at `OPERATE`/`VERIFY` = functional.
- **Depth** — how far up the stage spine a run executes (a coarse *quick vs. full* dial); the cost strategy lives in #8.
- **Risk tier** — the danger an operation carries, **declared per operation** by the handler: **R0** read · **R1** safe mutation (create dashboard) · **R2** destructive (delete dashboard) · **R3** catastrophic (delete workspace). Gated against a Target's safety class; **R3 is globally forbidden**. (Enforcement: the **safety gate**, ADR-0004.)
- **Safety class** — a Target's **explicit, authoritative** field capping the **max risk tier** it will run (`read-only` / `prod-for-us` / `sandbox`). **Never derived from the workspace name** (the label lies). Changing it is a high-friction, audited action. Rare audited per-target exceptions allowed; default none. (Enforcement matrix: #6.)
- **Tag** — an optional `key=value` label on a Target, used only as a **filter chip** to navigate the target list and for one-click multi-select — **not** a query language. `environment` (dev/stage/prod) is just a tag; safety class is the real control.
- **Target selection** — always **explicit, no implicit default**: GUI = a picker with "All" at the top + individual targets; API = named target(s) or explicit `"all"`; CI = the affected target(s) resolved from the Terraform stack being applied. Even "All" is safe — the per-target safety gate fires per target.
- **Identity handle** — a Target's reference to its **OAuth M2M service principal**, stored as a Databricks secret-scope `scope/key`. The app mints short-lived tokens on demand (never persisted); the SP secret rotates in the scope out-of-band. Each SP is least-privilege — never workspace-admin. (ADR-0003.)
- **Safety gate** — the enforcement that stops a Check operation whose **risk tier** exceeds the target's **safety class** cap. Three independent layers: (1) the run **engine** pre-dispatch, (2) the shared outbound **client wrapper** at call time, (3) the least-privilege **SP** in Databricks IAM. R3 is a wall through all three.
- **Safety override** — the human-only, GUI-only, per-run, typed-confirmation, audited action that authorizes an **R2** op on a `prod-for-us` target. **Never available to CI/automation.**
- **`BLOCKED_BY_SAFETY`** — the result status for an operation the safety gate stopped: not a pass, not a functional failure, surfaced loudly.
- **Handler manifest** — a handler's self-description: the canonical stages it maps to, its operations + each operation's **risk tier**, its **params schema**, its **structured assertions**, and its **evidence schema**. Powers the no-code GUI form, the AI authoring assistant, and the safety gate.
- **AI Authoring Assistant** — turns a **plain-English** intent ("start a cluster under policy P and confirm it's allowed") into a valid Test/Check, compiling to **deterministic** structured assertions where possible. (#8.)
- **Self-Service Center (SSC)** — a separate app (built in-house) that provisions workspaces/catalogs/service-principals; a **consumer** of this platform's pass/fail results, not a driver.
