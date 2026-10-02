"""The Assistant's Triage / RCA read-path (ADR-0012 §4, ADR-0009).

RCA is the same Assistant as the shelf's other turns — **different tools** (ADR-0012 §1). Its
tools (:mod:`app.services.assistant_tools`) are **strictly read-only**, and its
output is **advisory** and **suggest-never-assert**: it *proposes* an attributed
cause and a **lane placement**, and it **defaults to the Overnight lane when it
is not confident** (ADR-0009 §3). It never records an authoritative attribution
and never mutates state (user story 17/18).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from sqlmodel import Session

from app.services import assistant_tools

ModelCall = Callable[[list[dict]], str]

#: The two lanes of the "What's Interesting" view (ADR-0009). Overnight is the
#: default when cause can't be attributed to a specific deploy.
LANE_RECENT = "recent-deployments"
LANE_OVERNIGHT = "overnight"


@dataclass
class RcaSuggestion:
    """An advisory RCA proposal — a suggestion, never an assertion."""

    #: Always a *suggested* cause; the human stays the decision-maker.
    suggested_cause: str = ""
    #: Suggested lane; defaults to Overnight absent confident attribution.
    suggested_lane: str = LANE_OVERNIGHT
    confidence: str = "low"  # low | medium | high
    #: Read-only evidence the suggestion leaned on (provenance thread, history).
    evidence: dict = field(default_factory=dict)
    model_available: bool = True
    #: Marks the whole result advisory — RCA never gates and never asserts.
    advisory: bool = True

    def to_dict(self) -> dict:
        return {
            "suggested_cause": self.suggested_cause,
            "suggested_lane": self.suggested_lane,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "model_available": self.model_available,
            "advisory": self.advisory,
        }


_RCA_SYSTEM = (
    "You are the triage/RCA capability of a Databricks validation Assistant. Given "
    "a failing finding and read-only history, SUGGEST (never assert) a likely "
    "cause and a lane. Reply with a single JSON object: {\"cause\": \"...\", "
    "\"lane\": \"recent-deployments\"|\"overnight\", \"confidence\": "
    "\"low\"|\"medium\"|\"high\"}. If you cannot attribute the failure to a "
    "specific deploy, use \"overnight\"."
)


def suggest(session: Session, *, check_id: str, target_id: Optional[str] = None,
            turn: str = "", model_call: Optional[ModelCall] = None) -> RcaSuggestion:
    """Suggest a cause + lane for a finding using read-only tools (advisory)."""
    provenance = assistant_tools.commit_provenance_thread(
        session, check_id=check_id, target_id=target_id)
    history = assistant_tools.run_history(session, check_id=check_id, target_id=target_id, limit=10)
    evidence = {"commit_provenance_thread": provenance, "run_history": history}

    first_bad = provenance.get("first_bad") or {}
    # A first-bad tied to a commit sha is deploy-attributable → recent lane.
    has_commit = bool(first_bad.get("git_sha") and first_bad["git_sha"] != "unknown")

    if model_call is None:
        return _heuristic(evidence, first_bad, has_commit, model_available=False)

    messages = [
        {"role": "system", "content": _RCA_SYSTEM},
        {"role": "user", "content": json.dumps(
            {"turn": turn, "check_id": check_id, "target_id": target_id, "evidence": evidence})},
    ]
    try:
        reply = model_call(messages)
    except Exception:  # noqa: BLE001 - advisory; degrade to the heuristic
        return _heuristic(evidence, first_bad, has_commit, model_available=False)

    from app.services.assistant import parse_json_object

    plan = parse_json_object(reply)
    cause = str(plan.get("cause") or "").strip()
    lane = str(plan.get("lane") or "").strip()
    confidence = str(plan.get("confidence") or "low").strip().lower()

    # Suggest-never-assert: absent a confident, deploy-attributable cause, default
    # to Overnight (ADR-0009 §3). The model can only move us to the recent lane
    # when it is confident AND a commit actually backs the attribution.
    if lane != LANE_RECENT or confidence == "low" or not has_commit:
        lane = LANE_OVERNIGHT
    if not cause:
        return _heuristic(evidence, first_bad, has_commit, model_available=True)

    return RcaSuggestion(suggested_cause=cause, suggested_lane=lane,
                         confidence=confidence if confidence in ("low", "medium", "high") else "low",
                         evidence=evidence, model_available=True)


def _heuristic(evidence: dict, first_bad: dict, has_commit: bool,
               *, model_available: bool) -> RcaSuggestion:
    """Deterministic fallback: honest, low-confidence, Overnight-by-default."""
    if first_bad:
        bucket = first_bad.get("bucket_at_stage") or "an unclassified failure"
        if has_commit:
            cause = (f"first-bad run {first_bad.get('run_id')} shows {bucket}, "
                     f"following commit {first_bad.get('git_sha')}")
            return RcaSuggestion(suggested_cause=cause, suggested_lane=LANE_RECENT,
                                 confidence="medium", evidence=evidence,
                                 model_available=model_available)
        cause = f"failure ({bucket}) not attributable to a specific deploy"
        return RcaSuggestion(suggested_cause=cause, suggested_lane=LANE_OVERNIGHT,
                             confidence="low", evidence=evidence,
                             model_available=model_available)
    return RcaSuggestion(suggested_cause="no failing history found for this finding",
                         suggested_lane=LANE_OVERNIGHT, confidence="low",
                         evidence=evidence, model_available=model_available)
