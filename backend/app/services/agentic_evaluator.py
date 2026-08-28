"""The agentic evaluator — a pinned LLM judge over a Check's evidence (ADR-0008).

An ``agentic`` Check carries a plain-text assertion the deterministic compiler
*couldn't* turn into a structured predicate ("the warehouse came up
healthy-ish"). ADR-0008 says such a Check is judged by a **pinned model** and is
**always advisory** — it never gates CI. This module is that judge.

Two invariants are enforced *here*, not left to the model's goodwill:

* **The judge must cite the evidence field(s) it used** (ADR-0008 §4). A verdict
  that cites no field actually present in the Check's structured evidence is
  **not trusted**: the result is marked inconclusive (``pass=False``, uncited),
  so an un-grounded "pass" can never quietly stand. This keeps an advisory
  verdict inspectable in the derivation panel.
* **The result is advisory** — :attr:`AgenticVerdict.gating` is always ``False``.
  A caller (the run roll-up) treats it as signal only (ADR-0008 §1/§3); it can
  never be a fail-closed gate input, so a flaky judgement never blocks a deploy.

The judge reads only the Check's **structured evidence**, never free
re-observation, so the same evidence yields the same field to cite.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.core.enums import Evaluator, Expectation

ModelCall = Callable[[list[dict]], str]


@dataclass
class AgenticVerdict:
    """The advisory result of judging one agentic Check over its evidence."""

    passed: bool
    cited_fields: list[str] = field(default_factory=list)
    rationale: str = ""
    #: True only when the judge cited at least one field present in the evidence.
    cited: bool = False
    model_available: bool = True
    #: Always ``agentic`` / advisory — this evaluator never produces a gate input.
    evaluator: str = Evaluator.AGENTIC.value
    gating: bool = False

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "cited_fields": self.cited_fields,
            "rationale": self.rationale,
            "cited": self.cited,
            "model_available": self.model_available,
            "evaluator": self.evaluator,
            "gating": self.gating,
        }


_JUDGE_SYSTEM = (
    "You are the pinned advisory judge for a Databricks validation Check. You are "
    "given the Check's plain-text assertion and its STRUCTURED EVIDENCE (a JSON "
    "object). Decide whether the evidence satisfies the assertion. You MUST cite "
    "the exact evidence field name(s) you judged on. Reply with a single JSON "
    "object: {\"verdict\": \"pass\"|\"fail\", \"cited_fields\": [\"field\", ...], "
    "\"rationale\": \"...\"}. Judge only over the evidence given; never invent a "
    "field."
)


def _parse(reply: str) -> dict:
    from app.services.authoring import parse_json_object

    return parse_json_object(reply)


def evaluate(assertion: str, evidence: dict, model_call: Optional[ModelCall],
             expectation: Expectation = Expectation.PASS) -> AgenticVerdict:
    """Judge ``assertion`` against ``evidence`` with the pinned model (advisory).

    ``model_call`` is the pinned Model-Serving caller; when it is ``None`` or
    fails, the verdict degrades to an uncited, inconclusive advisory result
    (never a pass) so a missing model never manufactures a green signal.
    """
    evidence = evidence or {}
    if model_call is None:
        return AgenticVerdict(passed=False, cited=False, model_available=False,
                              rationale="No pinned judge available; advisory-inconclusive.")

    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": json.dumps({"assertion": assertion, "evidence": evidence})},
    ]
    try:
        reply = model_call(messages)
    except Exception:  # noqa: BLE001 - advisory only; degrade, never raise into a run
        return AgenticVerdict(passed=False, cited=False, model_available=False,
                              rationale="Judge call failed; advisory-inconclusive.")

    plan = _parse(reply)
    raw_fields = plan.get("cited_fields") or plan.get("cited_field") or []
    if isinstance(raw_fields, str):
        raw_fields = [raw_fields]
    # Enforce citation: keep only fields the judge actually cited AND that are
    # present in the structured evidence. An un-grounded citation doesn't count.
    cited_fields = [f for f in raw_fields if f in evidence]
    cited = bool(cited_fields)

    verdict_pass = str(plan.get("verdict", "")).lower() == "pass"
    if expectation == Expectation.DENY:
        verdict_pass = not verdict_pass

    # An uncited verdict is never trusted as a pass (ADR-0008 §4).
    passed = verdict_pass and cited

    return AgenticVerdict(
        passed=passed,
        cited_fields=cited_fields,
        rationale=str(plan.get("rationale", "")),
        cited=cited,
        model_available=True,
    )
