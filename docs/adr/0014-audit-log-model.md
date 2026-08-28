# ADR 0014 — Audit-log model (the standing control on config mutations)

- **Status:** Accepted (2026-08-28). Resolves [#17](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/17).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

ADR-0010 ships **no preventive access gate**: every authenticated human app user has full capability, and the controls on the two safety-gate-defeating edits (safety-class downgrade, safety override) are *ceremonial* (friction ceremony) and *forensic* (audit), not permissions. That makes the **audit trail the standing control** — it is what makes the no-roles model defensible. ADR-0013 (branding) already flagged that branding edits "join target registration / safety-class change / override as things the audit trail must record," reinforcing this fog patch.

Open questions the model had to fix: which events are recorded, what each record captures, where it lives (relative to run history — the retained-history model #16), retention, and how it's surfaced. The effort's simplicity / OSS litmus principles pull toward the smallest thing that is a real control.

## Decision

1. **One append-only Lakebase table.** The audit log is a single **append-only** table in the Lakebase-authoritative store (ADR-0001), separate from the run/result history (#16). *Append-only* — no in-app edit or delete path — is the integrity property that makes it a control rather than editable state.
2. **Scope: config-plane mutations only — not runs.** Runs are already recorded by the retained-history model (#16), so the audit log is **not** a run log. It records the configuration changes that have **no other gate** (ADR-0010):
   - target **register / edit / deregister**
   - **safety-class change** (before→after; flagged when the change is a *downgrade* — the dangerous direction)
   - **safety override** (per-run R2-on-`prod-for-us` authorization; carries the typed-ceremony reason)
   - **branding edit** (ADR-0013)
   - **Test create/edit** — recorded as a **pointer to the Lakebase version** rather than a duplicated diff, since Test content is already versioned (ADR-0006). *Runs themselves, and run triggers, are out of scope* — run history covers them.
3. **Each record captures:** the **actor** (host Databricks App identity — the workspace user or SP, per ADR-0010), a **timestamp**, the **event type**, the **entity reference** (which target / Test / the global branding record), a **before→after** snapshot (or a version pointer where the entity is already versioned), and — for the two friction-gated edits — the **ceremony reason string** the human typed.
4. **Retention: unbounded, never auto-pruned.** It is the standing control and the volume is tiny (config mutations, not runs). No retention window is set. Same deliberate-deferral posture as the queue-depth bound (ADR-0011): revisit only if it ever bites.
5. **Surfaced as a read-only GUI audit view** — filterable by **target · actor · event type**, reachable from a target's page (that target's history) and from a global settings page (all mutations). It is **exportable**, and because it is plain Lakebase, an adopter can also point their own query/dashboard at it — the same OSS-friendly stance ADR-0009/0001 take for run history. **No bespoke audit API** beyond reading the table.

## Consequences

- **+** Makes ADR-0010's no-roles access model **defensible**: every gate-defeating action leaves an immutable, attributed record, so "friction + audit" is a real control, not just a gesture.
- **+** **Append-only + Lakebase** means no new infrastructure and a tamper-evident trail; **config-plane-only scope** keeps it small and legible (a stranger sees exactly the mutations that matter).
- **+** Reusing **version pointers** for Test edits avoids duplicating the diff that ADR-0006's versioning already stores.
- **−** Append-only + unbounded retention means the table only grows; acceptable given config-mutation volume, but flagged for the same "revisit if it bites" treatment as queue-depth.
- **−** The GUI audit view is a new build surface (filterable table, per-target and global entry points); modest, and it reuses the existing poll/read patterns.
- **Note:** with the audit-log model fixed, three of the config mutations it records (safety-class change, override, branding edit) already had "audited" asserted by ADR-0010/0013 — this ADR is what those forward references point to.

## Downstream

Consumes ADR-0010 (audit = the standing forensic control; actor = host App identity), ADR-0004/0006 (the safety-class change, override, and Test-version events it records), ADR-0013 (branding edits are audited config mutations), ADR-0001 (Lakebase-authoritative append-only store), and sits **beside** the retained-history model (#16) — separate table, same store family. Updates CONTEXT.md (new **Audit log** term). No new tickets surfaced — the decision is self-contained; the sibling frontier tickets (retained-history #16, notifications #18) remain open.
