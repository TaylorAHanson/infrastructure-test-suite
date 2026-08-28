# ADR 0009 — "What's Interesting" triage view

- **Status:** Accepted (2026-08-28). Resolves [#10](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/10).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

The destination calls for a human-centric *"what regressed since my change, and why"* view across ~100 targets — surfacing the failure taxonomy (ADR-0001), flaky detection, and diff-vs-last-good, with **signal-over-noise ranking** so an engineer sees the few things that matter, not 100 green rows. This was a **prototype** ticket: three structurally-different layouts were built and reacted to (triage feed / fleet matrix / change-diff), then the winner was pared down over two iterations for density. Prototype asset: `docs/prototypes/whats-interesting.prototype.html` (`?variant=A|A2|A3|B|C`; **A3** is the accepted direction).

## Decision

1. **Triage-feed direction** — a ranked/grouped list of **findings** (not raw runs, not a fleet matrix, not a single change-diff). The matrix and change-diff layouts were rejected as too dense/intimidating for the zero-training bar.
2. **Two lanes, one screen** — the same activity (regression triage) at two **scopes**, shown together rather than behind a mode switch:
   - **Recent-deployments lane** (commit-scoped): a 24h, newest-first list of CI-triggered Runs, **bounded in height with infinite scroll** so deploy volume can never push the rest of the page down. Answers *"CI emailed me — what's up?"*
   - **Overnight lane** (time-window scoped): fleet drift **not attributable to a specific deploy** (e.g. an infra/metastore change). Answers *"what happened while I was away?"*
3. **Attribution drives lane placement.** Which lane a regression lands in is a causal judgment (your commit vs. infra drift vs. flaky). Absent attribution, default to the time-window (Overnight) lane. Doing this *well* requires history-querying intelligence → the **RCA assistant** (a newly-surfaced decision, its own ticket).
4. **Progressive disclosure throughout — nothing auto-expands.** Every item is a collapsed fold; scan first, dive on click. The collapsed row shows only: severity dot · what broke · #targets · since-which-commit.
5. **Commit provenance is the troubleshooting spine.** Every finding threads **last-green → first-bad** — a commit `sha`, or a non-deploy event ("metastore grant change · 21:14"). This is the destination's *diff-vs-last-good*, expressed as **when it started**, which is the fast path to a cause.
6. **Signal-over-noise ⇒ no non-actionable numbers.** Nominal / recovered / pre-existing collapse into a single muted, **clickable** "Everything else" footer that reveals detail on demand — never a static, unclickable count presented as a finding.
7. **Failure taxonomy surfaced as `bucket @ stage`** over the canonical spine (ADR-0001). **Connectable/functional** is demoted to expand-only (it's a derived view). **Flaky** is shown as an oscillation sparkline and kept out of the primary signal (advisory only — ADR-0008).
8. **Reuses the shared model** — Runs/Verdict (ADR-0005), depth `quick`/`full` (ADR-0007), per-target safety class (ADR-0002/0004). CI `full` and nightly `full` both feed this view; **advisory (`agentic`) results appear here as signal, never as a gate** (ADR-0008).

## Consequences

- **+** One screen answers both the reactive ("my deploy failed") and ambient ("overnight drift") questions with no mode switch.
- **+** Progressive disclosure + bounded scroll keep the view calm at ~100 targets and high deploy volume — meeting the "not intimidating / zero-training" bar the effort's Notes set.
- **+** Commit-threading makes *"when did this start"* the primary troubleshooting affordance.
- **−** The lane split leans on **cause attribution**, which needs history-querying intelligence — a new dependency on the **RCA assistant**. Until that exists, attribution is heuristic (shared-SP / shared-metastore correlation, commit coincidence).
- **−** Diff-vs-last-good requires retaining a per-`(target, check)` **last-good verdict + recent verdict history** — a storage/query requirement on the run store.

## Downstream

Surfaces a new decision — the **RCA assistant** (tools to query commit history + prior failures; cause attribution that sorts the lanes; assert-vs-suggest boundary; inline-in-this-view vs. a general Q&A surface; relationship to the #8 authoring assistant). Consumes the ADR-0001 taxonomy and ADR-0008 advisory results. Prototype (throwaway) captured at `docs/prototypes/whats-interesting.prototype.html`.
