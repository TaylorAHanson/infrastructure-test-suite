# ADR 0019 — The Assistant's turn is a LangGraph tool-calling loop

- **Status:** Accepted (2026-08-31). Part of [#56](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/56); implemented by [#57](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/57). **Amended (2026-09-01)** by [#112](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/112): the authoring capability may abstain into a conversational reply for a non-authoring turn — see Decision §6.
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

The Assistant's `/turn` (ADR-0012) routed with a two-mode **keyword classifier** (`_resolve_mode`): it guessed *authoring* vs *RCA* from words in the turn (and whether a finding was on the page), then handed the turn to one service that made a single-shot model call. That classifier swallowed plain questions ("do we have a test for X?", "what happened last run?") into the authoring flow's clarifying-question fallback, and it hard-coded which tools ran where — the model never got to decide what grounding it needed. ADR-0006 already committed the write-path to **tool-calling** grounding; the classifier undercut that by making the routing, not the model, choose the tools.

## Decision

1. **`_resolve_mode` is removed.** The keyword classifier is deleted entirely; a turn is a real **tool-calling agent**, not a pre-classified single-shot call.
2. **A LangGraph `StateGraph` loop drives the turn.** Two nodes — *agent* (call the model with the tool schemas) and *tools* (execute the tool calls the model chose) — loop until the model answers with no further tool call, bounded by a hard iteration cap. Plain **LangGraph** (`langgraph.graph.StateGraph`), no `langchain-databricks`/`ChatDatabricks` wrapper.
3. **Nodes call the pinned Model-Serving client directly.** `model_serving.py` is extended to pass a tool schema + `tool_choice` and read `tool_calls` back (via the OpenAI-shaped invocations response, since the SDK's typed `ChatMessage` drops `tool_calls`). The model is still reached only through the injectable `model_call` seam, which returns a JSON string — a `{"tool_calls": […]}` envelope for a tool step, else the final answer — so the whole loop stays stubbable.
4. **Behaviour is preserved.** Capability selection is now **contextual, not keyword-based**: an explicit `mode` wins; otherwise a finding under triage is an RCA context and everything else is authoring. The write-path still runs the GUI validator, refuses R3, and is suggest-then-human-confirms; the read-path (RCA) stays read-only and advisory. Grounding is still done deterministically before the draft is built, so a suggested run-as-role stays bounded to the target's registered roles even when the model doesn't call the grounding tools.
5. **Tool-call transparency is additive.** The `/turn` response keeps its `mode`/`draft`/`rca` contract and gains a `tool_calls` list (name/arguments/result of each executed tool); no existing field changes.
6. **The authoring capability may abstain into a conversational reply (#112).** "Capability is chosen from context, not keywords" (§4) still governs *routing* — the router never inspects the turn's words. But once a turn has landed in the authoring capability, the capability itself may recognise that the turn isn't an authoring request and abstain into a plain-English capability blurb (`mode="query"`, no draft) instead of compiling a clarifying-question Draft from a greeting. This abstention is layered: the authoring system prompt nudges a live model to reply with `{"answer": ...}` directly for a meta/greeting/chit-chat turn; a deterministic pattern match (`authoring.is_conversational_turn`) is the safety net when no model is configured or the model's reply is unusable. #111's weak-surface-match abstention (inside `build_draft`) remains a further-downstream fallback for a turn neither of those catches.

## Consequences

- **+** The model decides per turn what to ground on — the mechanism every later capability (Suite composition, Run submission, query answering) builds on, without a second agent or a bypass path.
- **+** The seam stays a `messages → str` call, so the existing `tests/test_assistant_api.py` override still works; the stub now scripts a *sequence* of tool calls rather than one JSON blob.
- **−** Adds a `langgraph` runtime dependency and a per-turn compiled graph. The loop is bounded by an iteration cap so a runaway model still terminates.
- **−** The pinned endpoint must speak OpenAI-style tool calling; when it doesn't (or none is configured), the loop degrades to the deterministic fallback exactly as the single-shot path did.
- **+** A meta/greeting/chit-chat turn gets a helpful capability blurb instead of a confusing "which operation?" clarifying question, on both the live-model and model-free installs (#112).
- **−** The abstention pattern list (`authoring._CONVERSATIONAL_PATTERNS`) is a second, narrower keyword surface living inside the authoring capability; it must stay scoped to genuinely non-authoring turns so it never swallows a real intent that happens to open with a greeting.

## Downstream

Amends ADR-0006 §6 (the write-path is now one node of the loop) and ADR-0012 (the "one tool-calling grounding harness" is now literal). The closed tool inventory is its own decision — ADR-0020. #112 amends this ADR (§6) to permit the authoring capability's conversational abstention, layered above #111's weak-surface-match abstention in `build_draft`.
