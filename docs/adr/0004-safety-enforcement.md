# ADR 0004 — Safety / blast-radius enforcement

- **Status:** Accepted (2026-08-27). Resolves [#6](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/6).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1).

## Context

Operations carry risk tiers R0–R3 (ADR-0001); targets carry an authoritative safety class (ADR-0002). It must be **impossible** for a Test to mutate a `read-only` or `prod-for-us` workspace beyond its cap, and impossible to ever perform an **R3** (catastrophic) op anywhere. This is the platform's most important safeguard — and the code ships open source, so handlers are effectively third-party and cannot be trusted to self-police.

## Decision

1. **Enforcement matrix:** `read-only` → max **R0**; `prod-for-us` → max **R1** (R2 only via override); `sandbox` → max **R2**. **R3 is never reachable** by any class or override.
2. **Primary gate — engine, pre-dispatch, per-operation.** Before each Check operation is dispatched, the engine compares the handler-declared risk tier to the target's cap. The UI only *previews* what will be blocked; the engine decides.
3. **Defense-in-depth — second, independent check at the shared client chokepoint.** Every Databricks call funnels through one trusted outbound **client wrapper** (handlers never hold raw credentials — tokens are minted centrally, ADR-0003), which **re-validates tier-vs-cap at call time on an independent code path**. A bug in the engine gate does not disable this one.
4. **Backstop (outside the app) — least-privilege SP** (ADR-0003): the target identity physically lacks permission to exceed its grants, containing even total app-logic failure via Databricks IAM.
5. **Override ("hard button"):** authorizing **R2 on `prod-for-us`** is a **human-only, GUI-only, per-run** action with **typed confirmation** (type the target name), fully audited. **Never available to CI or automation** — a pipeline can never trip a destructive op on a protected target.
6. **Blocked behavior:** a gated operation is not run and recorded as **`BLOCKED_BY_SAFETY`** — a distinct status, neither pass nor functional failure — surfaced loudly in results.
7. **Audit:** every gate decision is audited — allowed / blocked / overridden (who + confirmation) / **safety-class change**.

## Consequences

- **+** Three independent layers (engine, client chokepoint, IAM) make an accidental destructive mutation extremely unlikely; CI can never trigger one; R3 is a wall.
- **+** Makes "run All" (ADR-0002) and open-source handlers safe by construction — neither can self-authorize.
- **−** The single client chokepoint is now a hard architectural requirement: handlers must route all calls through it and never hold raw credentials.
- **−** Overrides are deliberately inconvenient (human-only, typed confirmation) — that's the point.

## Downstream

Feeds #7 (CI never gets the override; CI runs are capped by safety class) and #10 (`BLOCKED_BY_SAFETY` surfaced in the "what's interesting" view).
