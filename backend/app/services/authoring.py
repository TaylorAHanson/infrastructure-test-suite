"""Turning author input into a valid Check (compile + derive + validate) and the
NL→Test compiler behind the Assistant's write-path (ADR-0006/0017).

Two layers live here:

* the **deterministic write-path plumbing** shared by the no-code GUI and the
  Assistant — given the plain-text assertion + the open-SDK operation it
  **compiles** the assertion to a deterministic predicate (or flags it agentic),
  **derives** the coarse risk tier from the verb, and **refuses** a blocklisted
  operation (the R3-never floor);
* the **NL→Test compiler** (:func:`draft_from_intent`) — turns one plain
  sentence into a valid ``Draft`` proposal by grounding on the live SDK surface +
  the target/example catalogs (tool-calling, ADR-0017), asking the pinned model
  only to *draft*, then running that draft through **the same validator the GUI
  uses** (:func:`build_check_fields`). The model's output is **never trusted
  raw**: the operation is re-validated (R3 refused), the risk tier is derived
  here, and a suggested ``run-as-role`` is kept only if the target actually has
  it registered (ADR-0018). Guided-authoring suggestions (counterfactual, suite
  placement, run-as-role) are attached but **never applied** — suggest-then-
  human-confirms.

The Assistant's LLM only *drafts*; this module — not the model — decides the
evaluator, the risk tier, and what is refused, so the guarantees stay honest.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from app.core import sdk_surface
from app.core.enums import Expectation, RiskTier, SafetyClass
from app.core.operations import Operation
from app.handlers.registry import OperationBlocked, validate_operation
from app.models import Suite, Target
from app.schemas.schemas import CheckCreate, CheckUpdate, OperationIn
from app.services import assistant_tools
from app.services.assertion_compiler import compile_assertion
from app.services.risk import derive_risk_tier, needs_tier_confirmation

ModelCall = Callable[[list[dict]], str]


def build_check_fields(payload: CheckCreate) -> dict:
    """Compile + derive + validate a CheckCreate into Check column values.

    Raises :class:`app.handlers.registry.OperationBlocked` if the operation is
    on the blocklist.
    """
    operation = Operation.from_dict(payload.operation.model_dump()).to_dict()
    validate_operation(operation)  # R3-never floor

    compiled = compile_assertion(payload.assertion)
    expectation = payload.expectation or compiled.expectation

    return {
        "display_name": payload.display_name,
        "description": payload.description,
        "operation": operation,
        "handler_key": payload.handler_key,
        "assertion": payload.assertion,
        "expectation": expectation,
        "evaluator": compiled.evaluator,
        "compiled_predicate": compiled.predicate or {},
        "compilable": compiled.compilable,
        "expected_stage": payload.expected_stage,
        "risk_tier": derive_risk_tier(operation).value,
        "confirmed_risk_tier": payload.confirmed_risk_tier,
        "hints": payload.hints,
        "enabled": payload.enabled,
    }


def apply_check_update(payload: CheckUpdate) -> dict:
    """Return only the Check columns to update (recompiling as needed).

    Deliberately returns *just the changed/derived fields* — never id, version,
    or timestamps — so the caller can ``setattr`` them safely.
    """
    data = payload.model_dump(exclude_unset=True)
    fields: dict = {}

    if data.get("operation") is not None:
        operation = Operation.from_dict(data["operation"]).to_dict()
        validate_operation(operation)
        fields["operation"] = operation
        fields["risk_tier"] = derive_risk_tier(operation).value

    if data.get("assertion") is not None:
        compiled = compile_assertion(data["assertion"])
        fields["assertion"] = data["assertion"]
        fields["evaluator"] = compiled.evaluator
        fields["compiled_predicate"] = compiled.predicate or {}
        fields["compilable"] = compiled.compilable
        if data.get("expectation") is None:
            fields["expectation"] = compiled.expectation

    for key in ("display_name", "description", "handler_key", "expected_stage",
                "confirmed_risk_tier", "hints", "enabled", "expectation"):
        if data.get(key) is not None:
            fields[key] = data[key]
    return fields


# ---------------------------------------------------------------------------
# NL → Test compiler (the Assistant's write-path, ADR-0006/0017)
# ---------------------------------------------------------------------------
@dataclass
class CheckProposal:
    """One proposed Check inside a draft (already validator-passed)."""

    display_name: str
    operation: dict
    handler_key: str
    assertion: str
    expectation: str
    evaluator: str
    compilable: bool
    risk_tier: str
    kind: str = "assertion"  # "assertion" | "counterfactual"
    needs_tier_confirmation: bool = False

    def to_dict(self) -> dict:
        return {
            "display_name": self.display_name,
            "operation": self.operation,
            "handler_key": self.handler_key,
            "assertion": self.assertion,
            "expectation": self.expectation,
            "evaluator": self.evaluator,
            "compilable": self.compilable,
            "risk_tier": self.risk_tier,
            "kind": self.kind,
            "needs_tier_confirmation": self.needs_tier_confirmation,
        }


@dataclass
class DraftProposal:
    """The Assistant's suggested Draft Test — never persisted or run until a
    human confirms it (suggest-then-human-confirms, ADR-0006 §5/§8)."""

    name: str
    description: str
    checks: list[CheckProposal] = field(default_factory=list)
    #: A refused operation (R3/blocklisted) recorded, never dropped silently.
    refusals: list[dict] = field(default_factory=list)
    #: Guided-authoring suggestions, each suggest-then-confirm (never applied).
    suggested_counterfactual: Optional[dict] = None
    suggested_suite: Optional[dict] = None
    suggested_run_as_role: Optional[dict] = None
    clarifying_question: Optional[str] = None
    #: The audit-by-a-stranger record (user story 11).
    derivation: dict = field(default_factory=dict)

    @property
    def max_risk_tier(self) -> str:
        tiers = [RiskTier(c.risk_tier) for c in self.checks] or [RiskTier.R0]
        return max(tiers, key=lambda t: t.order).value

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "checks": [c.to_dict() for c in self.checks],
            "refusals": self.refusals,
            "max_risk_tier": self.max_risk_tier,
            "suggested_counterfactual": self.suggested_counterfactual,
            "suggested_suite": self.suggested_suite,
            "suggested_run_as_role": self.suggested_run_as_role,
            "clarifying_question": self.clarifying_question,
            "derivation": self.derivation,
        }


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


#: Deterministic patterns for a meta / greeting / chit-chat turn — not a request
#: to author, query, or manage anything (issue #112). This is abstention *inside*
#: the authoring turn (ADR-0019 §6), never a keyword classifier at the router:
#: the router still picks a capability from page context alone. It exists as
#: the model-free (or degraded-model) safety net — a live model is nudged by
#: :data:`app.services.assistant_graph._AUTHORING_TOOL_SYSTEM` to recognise the
#: same turns itself and reply with ``{"answer": ...}`` directly.
_CONVERSATIONAL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"^\s*(hi|hello|hey|howdy|yo|greetings)\b",
    r"\bhow('s| is| are) it going\b",
    r"\bhow are you\b",
    r"\bare you (working|there|online|up|alive)\b",
    r"\bwhat can you do\b",
    r"\bwhat do you do\b",
    r"\bwho are you\b",
    r"\bwhat (is|are) you\b",
    r"^\s*(thanks|thank you|ok|okay|cool|nice)\s*[.!?]*\s*$",
)]

#: The plain-English capability blurb a conversational turn gets back
#: (mode="query", no draft) — see :func:`is_conversational_turn`.
CONVERSATIONAL_ANSWER = (
    "Yes — I'm working. I can author a validation test from one sentence, "
    "check whether a test already covers something, explain why a check "
    "regressed, or help you build and run Suites. Just tell me what you'd "
    "like to do."
)


def is_conversational_turn(intent: str) -> bool:
    """True for a greeting/meta/chit-chat turn that isn't an authoring request.

    A deterministic pattern match, not the router's capability choice (that
    stays context-only, ADR-0019 §4) — this only decides, *once a turn has
    already landed in authoring*, whether an unresolved plan should abstain
    into :data:`CONVERSATIONAL_ANSWER` rather than a clarifying-question Draft.
    """
    text = (intent or "").strip()
    return bool(text) and any(p.search(text) for p in _CONVERSATIONAL_PATTERNS)


_AUTHORING_SYSTEM = (
    "You are the authoring capability of a Databricks validation Assistant. Turn "
    "the user's plain-English intent into ONE Databricks SDK/API call plus a "
    "plain-text assertion. Reply with a single JSON object: {\"name\", "
    "\"description\", \"operation\": {\"service\", \"method\", \"params\"}, "
    "\"assertion\", \"handler_key\", \"run_as_role\" (or null), "
    "\"counterfactual\": {\"assertion\", \"params\"} or null, "
    "\"clarifying_question\" (or null)}. Ground your operation on the provided "
    "SDK surface and examples; never invent a role."
)


def _grounding(session: Session, intent: str, target: Optional[Target]) -> dict:
    """Run the write-path grounding tools for an intent (ADR-0017)."""
    return {
        "sdk_surface": assistant_tools.search_sdk_surface(intent),
        "examples": assistant_tools.search_examples(session, intent),
        "roles": (assistant_tools.search_roles(session, target.id) if target else []),
    }


def _ask_model(model_call: Optional[ModelCall], intent: str, grounding: dict) -> tuple[dict, bool]:
    """Ask the pinned model to draft; return ``(plan, model_available)``.

    Any failure (no endpoint, error, unparseable) degrades to ``({}, False)`` so
    the deterministic fallback still produces a usable draft — the open-source
    install works without a model, and tests stub this cleanly.
    """
    if model_call is None:
        return {}, False
    messages = [
        {"role": "system", "content": _AUTHORING_SYSTEM},
        {"role": "user", "content": json.dumps({"intent": intent, "grounding": grounding})},
    ]
    try:
        reply = model_call(messages)
    except Exception:  # noqa: BLE001 - model is best-effort; fall back deterministically
        return {}, False
    plan = parse_json_object(reply)
    return plan, bool(plan)


#: Minimum term-coverage (0..1, see :func:`app.core.sdk_surface.top_match_confidence`)
#: the top SDK-surface hit must clear before the deterministic fallback adopts
#: it unconditionally. Below this, the match is a weak/fuzzy one (a greeting, a
#: meta-question, gibberish that happens to share a substring with a keyword) —
#: the fallback must abstain (→ the clarifying-question proposal) rather than
#: silently author a bogus check from chit-chat. A genuine intent the model
#: didn't parse still clears this bar comfortably (most of its terms land on
#: one entry), so the model-free deterministic path stays green.
_MIN_SURFACE_CONFIDENCE = 0.34


def _resolve_operation(intent: str, plan: dict,
                       grounding: dict) -> tuple[Optional[dict], Optional[dict], str]:
    """Pick the operation to author: the model's, else the top grounded hit.

    Returns ``(operation_dict, source_surface_entry, name_hint)``. ``operation``
    is None when nothing could be resolved, or when the only grounded candidate
    is below :data:`_MIN_SURFACE_CONFIDENCE` — both cases fall through to a
    clarifying question rather than guessing.
    """
    surface = grounding.get("sdk_surface") or []
    plan_op = plan.get("operation") or {}
    service = str(plan_op.get("service") or "").strip()
    method = str(plan_op.get("method") or "").strip()

    source = None
    if service and method:
        source = assistant_tools.operation_reference(service, method)
        # Verb precedence: the model's explicit verb, else the SDK surface entry's
        # (grounding the verb on SDK truth), else inferred from the method name.
        verb = str(plan_op.get("verb") or (source or {}).get("verb") or "")
        op = Operation(service=service, method=method, verb=verb,
                       params=dict(plan_op.get("params") or {})).to_dict()
        name_hint = str(plan.get("name") or f"{service}.{method}")
        return op, source, name_hint

    if surface and sdk_surface.top_match_confidence(intent) >= _MIN_SURFACE_CONFIDENCE:
        top = surface[0]
        op = Operation(service=top["service"], method=top["method"],
                       verb=top.get("verb", ""), params={}).to_dict()
        return op, top, str(plan.get("name") or top["summary"])

    return None, None, str(plan.get("name") or "")


def _validated_check(display_name: str, operation: dict, handler_key: str,
                     assertion: str, safety_class: Optional[SafetyClass],
                     kind: str = "assertion") -> CheckProposal:
    """Run a proposed check through the *same validator the GUI uses*.

    Raises :class:`OperationBlocked` for an R3/blocklisted operation so the
    caller records a refusal rather than a check (ADR-0006 §4).
    """
    payload = CheckCreate(
        display_name=display_name,
        operation=OperationIn(**operation),
        handler_key=handler_key or "databricks_sdk",
        assertion=assertion,
    )
    fields = build_check_fields(payload)  # same validator; raises on R3
    return CheckProposal(
        display_name=fields["display_name"],
        operation=fields["operation"],
        handler_key=fields["handler_key"],
        assertion=fields["assertion"],
        expectation=Expectation(fields["expectation"]).value
        if not isinstance(fields["expectation"], str) else fields["expectation"],
        evaluator=fields["evaluator"].value if hasattr(fields["evaluator"], "value") else fields["evaluator"],
        compilable=fields["compilable"],
        risk_tier=fields["risk_tier"],
        kind=kind,
        needs_tier_confirmation=needs_tier_confirmation(fields["operation"], safety_class),
    )


def _suggest_suite(session: Session, tags: list[str]) -> Optional[dict]:
    """Suggest an existing suite by tag overlap; None if nothing fits."""
    suites = session.exec(select(Suite)).all()
    best: Optional[Suite] = None
    best_overlap = 0
    tagset = {t.lower() for t in tags}
    for s in suites:
        overlap = len(tagset & {t.lower() for t in (s.tags or [])})
        if overlap > best_overlap:
            best, best_overlap = s, overlap
    if best is not None:
        return {"suite_id": best.id, "name": best.name,
                "why": f"shares {best_overlap} tag(s) with the drafted Test"}
    return None


def _suggest_run_as_role(plan: dict, grounding: dict) -> Optional[dict]:
    """Offer a ``run-as-role`` ONLY if the target has it registered (ADR-0018).

    A role the model names but the target hasn't registered is dropped — the
    Assistant can never suggest an invented or class-disallowed role (user
    story 24).
    """
    roles = grounding.get("roles") or []
    if not roles:
        return None
    requested = str(plan.get("run_as_role") or "").strip()
    if requested:
        for r in roles:
            if requested in (r.get("group_id"), r.get("label")):
                return {"role": r, "why": "grounded on a role the target has registered"}
        return None  # requested role not registered → never invent it
    # No explicit request but roles exist: offer the least-privileged one as a
    # role-differentiated-counterfactual candidate (user story 23).
    return {"role": roles[0],
            "why": "the target registers this role; enables a role-differentiated counterfactual"}


def draft_from_intent(session: Session, intent: str,
                      target: Optional[Target] = None,
                      model_call: Optional[ModelCall] = None) -> DraftProposal:
    """Compile one plain sentence into a validated Draft proposal (ADR-0006).

    Grounds on the SDK surface + target/example catalogs, asks the pinned model
    to draft, then validates that draft through the GUI's validator, refuses R3,
    derives the risk tier, and attaches guided-authoring suggestions. Nothing is
    persisted or run — this is the *suggest* half of suggest-then-confirm.
    """
    grounding = _grounding(session, intent, target)
    plan, model_available = _ask_model(model_call, intent, grounding)
    return build_draft(session, intent, plan, model_available, target, grounding=grounding)


def build_draft(session: Session, intent: str, plan: dict, model_available: bool,
                target: Optional[Target] = None,
                grounding: Optional[dict] = None) -> DraftProposal:
    """Validate a model ``plan`` into a Draft proposal — the post-model half.

    Extracted so the tool-calling loop (ADR-0019) can drive the model itself and
    hand the resulting ``plan`` here. Grounding is still done deterministically
    (over the SDK surface + target/example catalogs) so a suggested run-as-role
    stays bounded to the target's registered roles even when the model didn't
    call the grounding tools. The model's output is never trusted raw: the
    operation is re-validated, R3 refused, and the risk tier derived here.
    """
    if grounding is None:
        grounding = _grounding(session, intent, target)
    safety_class = target.safety_class if target else None

    operation, source, name_hint = _resolve_operation(intent, plan, grounding)
    if operation is None:
        # A required slot (the operation) is unresolved — ask one focused
        # question rather than guess (hybrid interaction, ADR-0006 §1).
        return DraftProposal(
            name="", description="",
            clarifying_question=(
                plan.get("clarifying_question")
                or "Which Databricks operation should this validate? "
                   "(e.g. run a SQL query, list catalogs, create a cluster)"),
            derivation={"model_available": model_available, "intent": intent,
                        "grounding": grounding},
        )

    assertion = str(plan.get("assertion") or _default_assertion(source, intent))
    handler_key = str(plan.get("handler_key") or (source or {}).get("handler_key") or "databricks_sdk")

    proposal = DraftProposal(
        name=name_hint or "Untitled Test",
        description=str(plan.get("description") or intent),
    )

    # Build + validate the primary Check (never trust the draft raw).
    try:
        proposal.checks.append(_validated_check(
            name_hint or "Check", operation, handler_key, assertion, safety_class))
    except OperationBlocked as exc:
        proposal.refusals.append({"operation": operation, "reason": str(exc), "tier": "R3"})

    # Guided authoring: counterfactual (surface hint or model), suite, run-as-role.
    proposal.suggested_counterfactual = _counterfactual_suggestion(plan, source, operation, handler_key)
    proposal.suggested_suite = _suggest_suite(session, _tags_from(intent, source))
    proposal.suggested_run_as_role = _suggest_run_as_role(plan, grounding)

    proposal.derivation = {
        "model_available": model_available,
        "intent": intent,
        "chosen_operation": operation,
        "grounding_source": source,
        "grounding": grounding,
        "coarse_risk_tier": proposal.max_risk_tier,
        "target": ({"id": target.id, "name": target.name,
                    "safety_class": target.safety_class.value} if target else None),
    }
    return proposal


def _default_assertion(source: Optional[dict], intent: str) -> str:
    """A sensible default assertion when the model didn't give one."""
    lowered = intent.lower()
    if any(w in lowered for w in ("denied", "blocked", "fails closed", "not allowed")):
        return "the operation is denied"
    if source and "row_count" in (source.get("evidence_fields") or []):
        return "the query returns at least one row"
    if source and "status_code" in (source.get("evidence_fields") or []):
        return "HTTP status is 200"
    return "the operation succeeds"


def _counterfactual_suggestion(plan: dict, source: Optional[dict], operation: dict,
                               handler_key: str) -> Optional[dict]:
    """Draft a candidate counterfactual from the model or the surface hint."""
    cf = plan.get("counterfactual")
    if isinstance(cf, dict) and cf.get("assertion"):
        params = {**(operation.get("params") or {}), **(cf.get("params") or {})}
        cf_op = {**operation, "params": params}
        return {"assertion": cf["assertion"], "operation": cf_op,
                "handler_key": handler_key, "why": "the intent implies a denied counterpart"}
    hint = (source or {}).get("counterfactual")
    if isinstance(hint, dict):
        params = {**(operation.get("params") or {}), **(hint.get("params") or {})}
        return {"assertion": hint["assertion"],
                "operation": {**operation, "params": params},
                "handler_key": handler_key,
                "expected_stage": hint.get("expected_stage"),
                "why": "this operation declares a counterfactual pair"}
    return None


def _tags_from(intent: str, source: Optional[dict]) -> list[str]:
    tags: list[str] = []
    if source:
        tags.append(source.get("service", ""))
    for word in ("policy", "safety", "connectivity", "egress", "sql", "uc",
                 "governance", "platform"):
        if word in intent.lower():
            tags.append(word)
    return [t for t in tags if t]
