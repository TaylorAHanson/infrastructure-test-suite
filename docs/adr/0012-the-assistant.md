# ADR 0012 — The Assistant (one app-wide agent: authoring + RCA)

- **Status:** Accepted (2026-08-28). Resolves [#12](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/12).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

ADR-0009 (#10) surfaced the need for an **RCA / triage assistant**: something with tools to query commit history + prior failures, **attribute cause**, sort the "What's Interesting" lanes, and answer *"why did this start failing?"*. ADR-0006 (#8) had already specified an **AI Authoring Assistant** (plain-English → Test, grounded by read-only tool-calling, output never trusted raw, R3 refused, suggest-then-human-confirms). The open questions: distinct assistant or one; what tools/data it gets and whether read-only; assert-vs-suggest; whether ADR-0008's advisory rule binds it; and where it lives.

The effort's standing simplicity principle ("would a stranger understand this with no docs?") and zero-training bar pull hard against shipping *two* AI agents a user must tell apart.

## Decision

1. **One agent, not two.** Authoring and triage/RCA are the **same Assistant**, differentiated by the **tools** it's given, not by being separate agents. Two agents was rejected as a violation of the simplicity principle — "these are just tools."
2. **App-wide, standalone, page-aware.** The Assistant is **not** embedded in the "What's Interesting" view or any single surface. It's delivered as the **Assistant shelf** — a consistent, quick slide-out available on **every page**. Each surface hands it a declared **page context** (the current finding, the Test being edited, the target list on screen, …) so it can offer contextual suggestions grounded in what the user is looking at. *(How that context is serialized/injected is a build-time detail, not a charting decision.)*
3. **Guardrails ride on the tools, not the agent.** Merging into one agent does **not** loosen any safety contract:
   - **Write-path tools** (author / edit a Test) keep ADR-0006's contract in full — output runs through the **same validator as the no-code GUI**, **R3 is refused**, max risk tier is surfaced, and every write is **suggest-then-human-confirms**, producing a `Draft` in Test Studio.
   - **Read-path / RCA tools** — the **commit-provenance thread** (last-green→first-bad), **Run history**, the **failure archive** (prior occurrences of a `bucket @ stage`), and **handler manifests** — are **read-only**. Their diagnostic output is **advisory** (ADR-0008): it never gates CI and never mutates state.
4. **RCA is suggest, never assert.** The Assistant *suggests* a finding's attributed cause and proposed **lane placement** (which ranks/sorts the What's Interesting lanes) and fills the per-finding *"why did this start failing?"* answer. It never records an **authoritative** attribution; absent confidence, placement defers to the **Overnight** default (ADR-0009 §3). Advisory annotation only — never a fail-closed gate input.
5. **Display name is a branding setting, not a fixed term.** The ubiquitous term stays **the Assistant**; the name a customer *sees* is **runtime-configurable** (default `Assistant`) via a new **Branding settings** layer. That layer (app name, logo, Assistant display name, and further presentation odds-and-ends; Lakebase-stored; instant-apply) is its **own decision** — surfaced as a fresh ticket, not resolved here.

## Consequences

- **+** One agent = one mental model for a stranger, and one shared implementation: a single pinned **Model-Serving endpoint** (the DAB's), one tool-calling grounding harness, one "never trusted raw / advisory" discipline — reused across both capability areas.
- **+** Per-tool contracts keep the authoring **write-path** guardrails (validator, R3 refusal) fully intact while the RCA **read-path** stays read-only and advisory. Unifying the agent never widens what it can do to a target.
- **+** An app-wide, page-aware **shelf** makes the Assistant reachable everywhere without a mode switch, and contextual suggestions lift it above a blank chat box — meeting the zero-training bar.
- **−** The Assistant now spans two capability areas. ADR-0006's "AI Authoring Assistant" is **reread as the Assistant's authoring capability**; ADR-0006 still stands as the authoring contract, and CONTEXT.md is updated to the unified term.
- **−** Branding the display name (and app name / logo / …) introduces a new runtime + Lakebase presentation concern that must apply instantly — surfaced as its own ticket rather than resolved here, so the Assistant ships with a sensible default name until that lands.

## Downstream

Surfaces the **Branding settings** decision (runtime-configurable presentation layer: app name, logo, Assistant display name, extensible; Lakebase-stored; instant-apply; scoping/logo-storage/theming extent all open) → new ticket. Reframes ADR-0006's assistant as one capability of the single Assistant. Consumes ADR-0009 (lanes + commit provenance), ADR-0008 (advisory rule), ADR-0001 (failure taxonomy). Updates CONTEXT.md (unified **Assistant** term; **Assistant shelf**; **Assistant page context**; the RCA stub resolved).
