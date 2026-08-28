# ADR 0016 — Failure alerting: a single generic run-completion webhook

- **Status:** Accepted (2026-08-28). Resolves [#18](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/18).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

Notifications were held as fog pending the "what's interesting" view (#10 / ADR-0009); with that resolved, the question is takeable. Walking the decided contracts shows most consumers need **nothing pushed**: **CI** polls the Run API and owns its own poll/timeout loop (ADR-0005); the **SSC** needs only the pipeline pass/fail (ADR-0005); **humans** read the "what's interesting" view (#10). The one genuine gap is **overnight drift** — a nightly regression with no deploy and no one polling, surfaced by #10's Overnight lane only when a human opens the page. For a regression-catching platform, "you have to remember to look" is too weak.

The effort's OSS litmus and simplicity bias rule out a baked-in Slack/email integration (per-provider secrets, formatting, opinion). The map's own lean was "an event/webhook on run completion, not a baked-in Slack/email integration."

## Decision

1. **One generic outbound webhook — no baked-in provider integrations.** The core emits an HTTP POST to a configured URL; pointing that at Slack / PagerDuty / email is the adopter's receiver, not the platform's concern. No provider secrets, no per-channel formatting in core.
2. **Config is a Lakebase record** (a webhook URL, or a small set), **editable by any authenticated human** (ADR-0010) and **audited** as a config mutation (ADR-0014 — the webhook sink is itself a config-plane change). No new infrastructure beyond the existing Lakebase store.
3. **Fires when a completed run produces ≥ 1 finding** — exactly when the "what's interesting" view (#10) would surface something. **Silent on all-green runs**, honoring #10's "no non-actionable numbers." A config toggle can widen firing to **every run completion** for adopters who want a heartbeat. Firing reuses #10's finding computation + #16's last-good; it invents **no** new regression logic.
4. **Payload = the run verdict + the run's finding summary + a deep link** into the "what's interesting" view (run_id, target(s), overall verdict, `bucket @ stage` summary, commit / event ref, URL). Enough to triage from the notification and click through.
5. **Best-effort delivery.** A small bounded retry on failure, failures logged; **no durable delivery queue or guarantee**. The webhook is a **nudge** — the view and Lakebase remain the source of truth — so at-least-once best-effort is deliberately sufficient (a durable delivery system would be over-building; cost/complexity is a non-goal, ADR-0007).

## Consequences

- **+** Closes the **overnight-drift gap** with the minimum, most OSS-idiomatic push — a stranger wires it to their own receiver with no platform-side integration.
- **+** **Zero new infra** (Lakebase config + an outbound POST) and **zero new signal logic** (reuses #10 findings, #16 last-good); default-silent-on-green means it inherits #10's actionable-only ethos.
- **+** Keeps CI/SSC contracts **untouched** — they still poll / read pass/fail; the webhook is purely additive, for humans.
- **−** Best-effort delivery means a missed POST (receiver down) is genuinely missed; acceptable because the view is the source of truth, but adopters must not treat the webhook as an audit-grade guarantee.
- **−** One more config surface + payload schema to document for OSS; mitigated by a single default-off-when-green behavior and a self-describing payload.
- **Note:** an adopter needing richer routing (per-target sinks, severity fan-out) writes it in their receiver, or points the webhook at a small relay — deliberately out of core.

## Downstream

Consumes ADR-0009 (#10 findings define *when* it fires and the payload's deep link), ADR-0015 (#16 last-good drives regression/finding detection), ADR-0005 (verdict payload; CI/SSC contracts stay poll/pass-fail), ADR-0010 (any human edits the sink config), ADR-0014 (the sink config is an audited config mutation), and ADR-0007 (cost/complexity non-goal → best-effort, no durable queue). Updates CONTEXT.md (new **Run-completion webhook** term). No new tickets surfaced — this was the last graduated frontier ticket; the only remaining fog is the deliberately-deferred **queue-depth / run-deadline bound**.
