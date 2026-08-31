# ADR 0021 — Creation entry points self-disclose that they open a conversation

- **Status:** Accepted (2026-08-31). Part of [#56](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/56); implemented by [#59](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/59) (Test Studio entry point + shelf seed-prompt mechanism), [#66](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/66) (Suites entry point), [#60](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/60) (starter-prompt onboarding), and [#61](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/61) (default catalog content).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

The Assistant is the **only** way to create a Test or a Suite — there is no manual create form, and by design (below) there won't be one. That is fine for someone who already knows it, and invisible to everyone else. A first-time user who downloads this open-source platform with zero training hits three walls at once: the Suites page had **no create affordance at all**; the only hint that creation happens through conversation was empty-state text that disappears the moment the catalog is non-empty; and even where a "New" control existed, a generic `+` reads as "open a form," so the conversational shelf that actually appears is a surprise. On top of that, a fresh install seeded an **empty** catalog, so the product's first impression was a blank slate rather than a working system. Nothing in the UI disclosed the interaction model, so the creation path was undiscoverable to exactly the audience an OSS platform most needs to onboard.

## Decision

1. **Persistent, self-disclosing creation entry points.** Both Test Studio and the Suites page carry a **persistent** "New" control — not gated on empty state — labelled *"Describe a new test"* / *"Describe a new suite"* and carrying a chat-style icon (`MessageSquarePlus`), never a bare `+`. The label and icon signal, *before the click*, that a conversation opens rather than a form. Clicking it opens the Assistant shelf with the input pre-seeded (`"Create a new test that …"` / `"Create a new suite that …"`) — an editable starting point, not a submitted turn.
2. **The Assistant stays the only creation path.** No manual (non-Assistant) create form for Tests or Suites is introduced. The entry points above route *into* the shelf; they are discovery affordances for the conversational flow, not an alternative to it. Creation therefore always runs through the suggest-then-human-confirms path (ADR-0006, ADR-0019), with no second code path that skips it.
3. **Starter prompts on an empty conversation.** When the shelf opens with no conversation yet, it renders 3–4 clickable starter prompts drawn from the **worked-example library** via the existing `search_examples` / `GET /assistant/examples` path (queried for the onboarding set). Clicking one pre-fills the input without auto-sending, so a novice sees concrete examples of what they can ask or request.
4. **A fresh install ships a working-looking product.** The seed script populates a curated default catalog of Tests and Suites (not empty) and a deliberate set of onboarding **worked-example** entries, so the starter prompts a first-time user sees are authored, not generic placeholders, and the app looks like a working system on first run.

## Consequences

- **+** The conversational creation model is discoverable without training: a persistent, labelled affordance on every creation surface, plus concrete example prompts, plus a non-empty catalog to explore.
- **+** Keeping the Assistant the sole creation path means one validated, confirm-gated flow to reason about — a manual form would have been a second surface to keep in sync with the validator and the safety gate.
- **−** Onboarding quality now depends on seed content: the curated worked-example set is load-bearing for the first-run experience, so its count is pinned by a seed-count assertion and must be maintained deliberately (see ADR-0006's worked-example library).
- **−** The signal is presentation-level (icon + label + seed prompt); it conveys the interaction model but does not *enforce* it, so future creation surfaces must repeat the same treatment rather than inherit it automatically.

## Downstream

Consumes ADR-0019 / ADR-0012 (the Assistant shelf and the tool-calling turn the entry points open into) and ADR-0006 (suggest-then-human-confirms authoring, and the worked-example library the starter prompts are drawn from). The tool inventory those turns may reach is bounded by ADR-0020.
