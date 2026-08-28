# ADR 0018 — Assume-role identity model (user-level / role-profile testing)

- **Status:** Accepted (2026-08-28). Resolves [#26](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/26). **Amends ADR-0003, ADR-0004.**
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

ADR-0003 authenticates every Run as the target's own least-privilege OAuth M2M service principal, which only ever answers *"can **this SP** do X?"*. The platform's value rests on **counterfactuals** (ADR-0001): *"as an admin profile → allowed"* **and** *"as an analyst profile → denied, fails closed"*. Making the **denied** side real requires the Run to act as a **permission profile / role**, not merely as the SP.

Research [#25](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/25) (branch `research/assume-role-mechanics`) settled the mechanics: Databricks RBAC lets an M2M OAuth SP **assume a role (group)** by adding `assume_group=<group-id>` to its `client_credentials` token request — non-interactive (CI-safe), **workspace-level only**, token **1 h**, the role's grants **replace** the SP's for the token's life, audited as `run_by`+`run_as`, **role/group granularity only** (no named-user). Maturity: **Preview**. The one caveat — the RBAC limitations note *"an app's service principal cannot yet assume a role for M2M"* — **does not apply here**: every per-target SP is **external to the platform app** (a standalone OAuth SP, ADR-0003), never the app's own runtime SP.

**ADR-0017 (open-SDK) landed concurrently** and made the **least-privilege identity the *primary* blast-radius bound** (per-operation risk tier demoted to a coarse verb-derived *secondary* cap). That raises the stakes here: an assumed role can be *broader* than the base SP, so the assumed identity must never be allowed to out-run the target's safety class.

## Decision

1. **Optional per-Test `run-as-role`.** A Test's `run-as-role` **defaults to the target's own SP** (ADR-0003, unchanged for anything that does not opt in); optionally it names **exactly one** registered role. This **amends ADR-0003** identity to **`(target, assumed-role?)`**. Assumption uses #25's native mechanism (`assume_group` on the workspace-level token request) — **no new credentials**, the SP stays the authenticating identity.

2. **Roles are registered *with the target*, bounded by safety class.** A target's set of assumable role-profiles is defined in the **same audited setup act** as the target itself (audited per ADR-0014). **Which roles may be attached is capped by the target's safety class** — a `read-only` target may attach only read-only roles, `sandbox` wider, etc. A Test can therefore **never assume a role that out-runs its target's class**.

3. **Safety composition (amends ADR-0004, under ADR-0017's identity-primary model).** Because ADR-0017 made the least-privilege identity the *primary* bound and the role's grants *replace* the SP's, the **safety-class bound on registerable roles (§2) is load-bearing** — it is what keeps the primary bound intact under assumption. On top, unchanged and identity-independent: the **blocklist R3-never floor** and the **coarse verb-derived tier + one-confirm-on-mutating-targets** secondary cap (ADR-0017). This is exactly the *"complementary, not compounding-risk"* composition ADR-0017 anticipated for #26 — **assumed-role least-privilege is the primary safeguard, not a defeat of it.**

4. **Role/group granularity only.** No named-user impersonation — forced by the platform (#25) and sufficient: role-profiles are precisely what policy questions (*"is the analyst profile denied?"*) are about.

5. **CI-safe by construction.** Assumption is a non-interactive token-request parameter, so `run-as-role` Runs behave identically under the **nightly-all**, **on-demand-API**, and **CI-caller** paths (ADR-0005 unchanged). Audited natively as `run_by` (SP) + `run_as` (role), composing with ADR-0014.

6. **Preview maturity accepted.** The capability is Databricks **Preview**; that is a build-time note, not a design blocker — the app-SP limitation is out of scope by construction (external SPs, §Context).

## Consequences

- **+** Counterfactuals become **fully real at the profile level** — the *denied-as-analyst* assertion the platform exists to make.
- **+** **Complementary with open-SDK** (ADR-0017): the assumed-role identity is the very thing that bounds an arbitrary SDK call's blast radius.
- **+** No new credentials, native audit (`run_by`/`run_as`), CI-safe — nothing new to operate.
- **−** The target-registration flow gains a **per-target role list**, and its **safety-class bound is now a load-bearing control** (must be enforced at registration and audited, ADR-0014).
- **−** Depends on a **Preview** feature (role assumption); the built platform must tolerate its GA/behavior shifts.
- **−** The **Assistant** (ADR-0006/0012) must ground on a target's registered roles to suggest a `run-as-role` (a per-target roles catalog), and counterfactual pairs (ADR-0001/0006) may now differ **by role** as well as by operation.

## Downstream

Amends **ADR-0003** (identity → `(target, assumed-role?)`; the assumable-role set is a safety-class-bounded, audited target attribute) and **ADR-0004** (assumed-role least-privilege is the primary bound under ADR-0017; safety-class-bounded role registration keeps it intact). Relies on **#25** (mechanics) and composes with **ADR-0017** (open-SDK, identity-primary safety) and **ADR-0014** (audit). Updates CONTEXT.md (**Run-as-role** / **Role-profile** added; **Identity handle** amended). **Spec amendment required:** **#20** (targets/identity/safety — role registration + safety-class bound) and **#22** (Assistant — roles grounding); folds into the consolidated **#20/#22** revision ADR-0017 already called for.
