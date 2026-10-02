"""The Assistant harness — one agent, differentiated by tools (ADR-0012).

There is exactly **one** Assistant: one pinned Model-Serving endpoint and one
tool-dispatch layer. Every surface hands the Assistant a **page context**;
:func:`run_turn` delegates the turn to the LangGraph tool-calling loop
(:mod:`app.services.assistant_graph`, ADR-0019), which picks the capability —
answering from the catalog, **Triage/RCA** (:mod:`app.services.rca`), or handing
a request to write a Test over to the script editor — from that context and
lets the model choose which read tools to call.

Guardrails ride on the tools (ADR-0012 §3): every shelf tool is read-only and
advisory. Writing a Test happens in the script editor
(:mod:`app.services.editor_agent`), and a Test still doesn't run until a human
accepts it (:func:`accept_test`).

The model is reached through a single injectable ``model_call`` seam
(:func:`get_model_call`) so it can be stubbed in tests and degrade gracefully
when no endpoint is configured.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

from sqlmodel import Session

from app.core.enums import TestStatus
from app.core.ids import utcnow
from app.models import Test
from app.schemas.schemas import AssistantTurnIn
from app.services import pinned_versions
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

ModelCall = Callable[[list[dict]], str]


# ---------------------------------------------------------------------------
# Model-call seam (pinned Model-Serving endpoint; stubbable / degradable)
# ---------------------------------------------------------------------------
def default_model_call(messages: list[dict]) -> str:
    """Call the pinned Model-Serving endpoint with the Assistant's tool schemas.

    Returns a JSON string the tool-calling loop understands: a ``{"tool_calls":
    [...]}`` envelope when the model chose to call tools, otherwise the model's
    final answer text. The tool schemas are always offered
    (``tool_choice="auto"``); callers that want a plain answer — RCA — simply
    parse the object and ignore any envelope, so they degrade cleanly if the
    model reaches for a tool.
    """
    from app.clients import model_serving
    from app.services.assistant_graph import TOOL_SCHEMAS

    reply = model_serving.chat_with_tools(messages, tools=TOOL_SCHEMAS, tool_choice="auto")
    if reply.tool_calls:
        return json.dumps({"tool_calls": [tc.to_dict() for tc in reply.tool_calls],
                           "content": reply.content})
    return reply.content or ""


def get_model_call() -> Optional[ModelCall]:
    """FastAPI dependency: the pinned model caller, or None when unconfigured.

    Returning None (rather than a caller that raises) lets the services report
    ``model_available: False`` and use their deterministic fallback — the
    open-source install works with no endpoint. Tests override this dependency.
    """
    from app.clients.model_serving import default_endpoint

    if not default_endpoint():
        return None
    return default_model_call


def parse_json_object(text: str) -> dict:
    """Extract the first JSON object from a model reply (tolerant of fences)."""
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace.group(0) if brace else None
    if candidate is None:
        return {}
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Turn routing — a LangGraph tool-calling loop (ADR-0019)
# ---------------------------------------------------------------------------
def run_turn(session: Session, payload: AssistantTurnIn,
             model_call: Optional[ModelCall] = None) -> dict:
    """Run one Assistant turn through the tool-calling graph (ADR-0019/0012).

    The graph selects the capability from context (a finding under triage is an
    RCA context; an explicit ``mode`` wins) and lets the model choose which read
    tools to call. Delegated to :mod:`app.services.assistant_graph`.
    """
    from app.services import assistant_graph

    return assistant_graph.run_agent_turn(session, payload, model_call=model_call)


# ---------------------------------------------------------------------------
# Draft → Active
# ---------------------------------------------------------------------------
def accept_test(session: Session, test_id: str, actor: str = "api-user") -> Test:
    """Promote a Draft Test to Active (explicit human accept, ADR-0006 §8).

    A no-op-safe: accepting an already-Active Test just returns it. Bumps the
    version + snapshots + audits so the promotion is on the version history.
    """
    test = session.get(Test, test_id)
    if test is None:
        raise ValueError("test not found")
    if test.status == TestStatus.ACTIVE:
        return test
    before = pinned_versions.test_snapshot(session, test)
    test.status = TestStatus.ACTIVE
    test.version += 1
    test.updated_at = utcnow()
    session.add(test)
    session.commit()
    session.refresh(test)
    out = pinned_versions.record_test_version(session, test, actor)
    record_audit(session, actor=actor, action="test.accept", entity_type="test",
                 entity_id=test.id, summary=f"Accepted draft {test.name} → Active",
                 before=before, after=out)
    return test
