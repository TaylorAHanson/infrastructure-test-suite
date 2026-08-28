# ADR 0003 — Credential & per-target identity model

- **Status:** Accepted (2026-08-27). Resolves [#5](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/5).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1).

## Context

Tests bind to a target at run time (ADR-0002); the app must authenticate as **~100 distinct workspace identities**. No PATs (old design principle). Open source: no extra infrastructure dependency, and onboarding must need no training.

## Decision

1. **OAuth M2M service principal per target** — client-id + secret → short-lived tokens via the Databricks SDK. **No PATs** (long-lived, un-rotatable in practice, wrong for 100 targets).
2. **Secrets live in Databricks secret scopes** — native, zero extra deps, an OSS user already has them. A target's **identity handle = a `scope/key` reference**. External secret managers (Vault / AWS Secrets Manager) are a **documented advanced option**, not required.
3. **Test-connection-before-save** — the "add a target" flow captures creds (or a `scope/key` pointer), runs a built-in **R0 connectivity Check**, and **refuses to save until it passes**. A saved target is a proven-reachable target. (The no-training safeguard.)
4. **No token persistence** — tokens are minted on demand and cached **in-memory per target**. The long-lived SP secret **rotates in the scope** out-of-band; the app reads fresh, so rotation needs no app change.
5. **Least privilege** — each target SP gets minimal grants (documented per handler); **never workspace-admin**. Safety class (ADR-0002) caps the rest.
6. **Topology-agnostic** — the per-target-SP + `scope/key` design works whether the ~100 workspaces are in **one account or many**; account topology is deliberately *not* pinned. Cross-account **reachability** (networking/PrivateLink) is a Check/handler concern, not a credential concern.

## Consequences

- **+** No extra infra; native rotation; proven-reachable targets on save; works across accounts; least-privilege by default.
- **−** Operators must create + grant an SP per target — the price of per-target isolation; the docs must make this dead simple (OSS onboarding).
- **−** In-memory token cache means a cold app re-mints on first use per target (acceptable).

## Downstream

Feeds #6 (safety enforcement combines identity + safety class) and #7 (CI caller auth).
