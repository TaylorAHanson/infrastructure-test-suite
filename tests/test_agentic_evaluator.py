"""Service seam: the pinned agentic judge (ADR-0008).

Deterministic assertions over a stub judge: assert the field-citation + advisory
invariants the harness enforces, not the model's wording (spec #22).
"""
from __future__ import annotations

import json

from app.core.enums import Expectation
from app.services import agentic_evaluator


def _judge(payload: dict):
    def call(messages):
        return json.dumps(payload)
    return call


def test_cited_pass_is_advisory_and_carries_the_field():
    v = agentic_evaluator.evaluate(
        "the warehouse came up healthy-ish", {"row_count": 3, "state": "RUNNING"},
        _judge({"verdict": "pass", "cited_fields": ["state"], "rationale": "state is RUNNING"}))
    assert v.passed is True
    assert v.cited and v.cited_fields == ["state"]
    assert v.gating is False and v.evaluator == "agentic"  # never gates (ADR-0008)


def test_uncited_pass_is_not_trusted():
    # The judge claims "pass" but cites a field absent from the evidence — the
    # harness refuses to trust an un-grounded verdict (ADR-0008 §4).
    v = agentic_evaluator.evaluate(
        "looks good", {"row_count": 3},
        _judge({"verdict": "pass", "cited_fields": ["nonexistent"], "rationale": "vibes"}))
    assert v.cited is False
    assert v.passed is False
    assert v.gating is False


def test_no_model_degrades_to_inconclusive_advisory():
    v = agentic_evaluator.evaluate("healthy-ish", {"row_count": 1}, None)
    assert v.model_available is False
    assert v.passed is False and v.cited is False
    assert v.gating is False


def test_deny_expectation_flips_the_verdict():
    # A counterfactual (DENY): the judge saying the op "passed" means the denial
    # did NOT happen → advisory fail.
    v = agentic_evaluator.evaluate(
        "the request is denied", {"status_code": 200},
        _judge({"verdict": "pass", "cited_fields": ["status_code"]}),
        expectation=Expectation.DENY)
    assert v.cited is True
    assert v.passed is False
