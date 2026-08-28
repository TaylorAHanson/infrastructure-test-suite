# ADR 0013 — Branding settings (runtime white-label presentation layer)

- **Status:** Accepted (2026-08-28). Resolves [#15](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/15).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

ADR-0012 (#12) made the **Assistant's display name** runtime-configurable and named it the *first brandable field*, punting the broader layer to its own ticket. An OSS adopter (or a customer environment) should be able to **rebrand the app without a rebuild**. The open questions: which fields are brandable and where the line sits (presentation vs behaviour); the instant-apply propagation model; scope (global vs per-tenant); logo storage; defaults for an un-branded install; and who may change it.

The effort's standing simplicity / under-ask principle pulls against an open-ended "settings" surface; the OSS litmus (a stranger understands it with no docs) and ADR-0010's no-app-local-roles model both bear on the answer.

## Decision

1. **One global record, fixed closed schema, presentation-only.** Branding is a **single global Lakebase record** for the deployment — not per-workspace/per-tenant (over-building for the single-app OSS shape; revisit only if multi-tenancy ever enters scope). Its schema is a **fixed, closed set** of presentation fields: adding a field is a code migration, **never** a runtime "add a setting." This closed schema is the wall that keeps branding **presentation only, never behaviour** — nothing here changes what the platform *does*, only how it looks and reads.
2. **Brandable fields (the "Broad" set):** **app name**, **logo**, **favicon**, **Assistant display name**, a **theme** (an **accent colour + a closed set of named design tokens**), and **footer/attribution text**. The theme is applied as **CSS custom properties from that closed token set** — **not** free-form CSS/JS injection (which would be behaviour, plus an XSS and accessibility hole). The token set must resolve in **both light and dark**; the design-system/dataviz default palette is its seed.
3. **Logo + favicon storage: Lakebase blobs** with tight constraints (**≤ 1 MB, PNG/SVG**), consistent with Lakebase-authoritative (ADR-0001). No object-store or workspace-files dependency — the OSS install stays self-contained.
4. **Instant-apply via navigation + the existing poll — no new push infra.** The app shell reads the branding record on load and the **existing polling loop** refreshes it. "Instant" means **no redeploy/restart**, visible on next navigation or within the poll interval. No websocket/SSE channel is added (rejected: push infrastructure an otherwise-polling app doesn't need, and more to document for OSS).
5. **Defaults ship seeded in the repo** so an un-branded install looks finished: app name `Validation Platform`, Assistant display name `Assistant`, a **text wordmark** when no logo is set, and the default token palette. The seed row ships as a migration.
6. **Who can change it: any authenticated human, no friction ceremony, but audited.** Per ADR-0010 there are no app-local roles — any authenticated **human** app user may edit branding. It gets **no friction ceremony**: presentation can't defeat a safety gate, so the ceremony stays reserved for the two safety-gate-defeating edits (safety-class downgrade + override). Every branding change is still **recorded in the standing audit trail** like other config mutations.

## Consequences

- **+** White-label (name, logo, favicon, theme, attribution) **without a rebuild** — an OSS adopter or customer env rebrands at runtime, meeting the open-source bar.
- **+** The **closed schema** + **closed token palette** + **no CSS injection** make "presentation only, never behaviour" enforceable rather than aspirational, and close the obvious XSS/accessibility hole a free-form theme would open.
- **+** No new infrastructure: a Lakebase blob and the poll loop already exist.
- **−** The full theme must be **theme-aware (light/dark) and accessible**; defining the closed token set and its light/dark resolution is a build concern, seeded from the dataviz/design palette.
- **−** Adds another audited config mutation, reinforcing the still-unspecified **audit-log model** (map fog) — branding edits join target registration / safety-class change / override as things the audit trail must record.

## Downstream

Consumes ADR-0012 (Assistant display name = the first brandable field), ADR-0010 (access model — any human, audited, no ceremony), and ADR-0001 (Lakebase-authoritative store, blob storage). Reinforces the **audit-log model** fog (branding is another audited config mutation). Updates CONTEXT.md (new **Branding settings** term). No new tickets surfaced — the decision is self-contained.
