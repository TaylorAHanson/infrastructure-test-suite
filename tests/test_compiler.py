"""Service-layer seams: assertion compiler, derive_connectivity, risk tiers.

These are the deep bits of logic that are awkward to assert end-to-end, so they
are unit-tested directly (spec #19 Testing Decisions).
"""
from __future__ import annotations

from app.core.enums import Evaluator, Expectation, RiskTier, SafetyClass
from app.core.operations import Operation
from app.core.stages import derive_connectivity
from app.services.assertion_compiler import compile_assertion, evaluate_predicate
from app.services.risk import derive_risk_tier, effective_risk_tier, needs_tier_confirmation


# --------------------------- assertion compiler ---------------------------
def test_row_count_assertion_compiles_deterministic():
    c = compile_assertion("the query returns at least one row")
    assert c.compilable and c.evaluator == Evaluator.DETERMINISTIC
    assert c.predicate == {"kind": "compare", "field": "row_count", "op": ">=", "value": 1}


def test_status_and_generic_comparison_compile():
    assert compile_assertion("HTTP status is 200").predicate["field"] == "status_code"
    assert compile_assertion("row_count >= 5").predicate == {
        "kind": "compare", "field": "row_count", "op": ">=", "value": 5
    }


def test_denial_assertion_is_a_counterfactual():
    c = compile_assertion("a non-compliant cluster is denied by policy P")
    assert c.evaluator == Evaluator.DETERMINISTIC
    assert c.expectation == Expectation.DENY
    assert c.predicate == {"kind": "denied"}


def test_non_compilable_assertions_are_flagged_agentic():
    for text in ("the warehouse came up healthy-ish", "the error message is clear and actionable"):
        c = compile_assertion(text)
        assert not c.compilable
        assert c.evaluator == Evaluator.AGENTIC
        assert c.predicate is None


def test_evaluate_predicate():
    gte1 = {"kind": "compare", "field": "row_count", "op": ">=", "value": 1}
    assert evaluate_predicate(gte1, {"row_count": 2}, True)
    assert not evaluate_predicate(gte1, {"row_count": 0}, True)
    assert not evaluate_predicate(gte1, {}, True)  # missing field never passes
    assert evaluate_predicate({"kind": "denied"}, {}, False)      # op denied => pass
    assert not evaluate_predicate({"kind": "denied"}, {}, True)   # op succeeded => fail
    assert evaluate_predicate({"kind": "ok"}, {}, True)
    assert evaluate_predicate(None, {}, True)  # no predicate => operation success


# --------------------------- derived connectivity ---------------------------
def test_derive_connectivity_views():
    assert derive_connectivity([{"stage": "AUTHENTICATE", "outcome": "FAIL"}]) == {
        "connectable": False, "functional": None
    }
    assert derive_connectivity([
        {"stage": "AUTHENTICATE", "outcome": "PASS"},
        {"stage": "OPERATE", "outcome": "PASS"},
    ]) == {"connectable": True, "functional": True}
    assert derive_connectivity([
        {"stage": "AUTHENTICATE", "outcome": "PASS"},
        {"stage": "OPERATE", "outcome": "FAIL"},
    ]) == {"connectable": True, "functional": False}
    # Nothing ran => both unknown.
    assert derive_connectivity([]) == {"connectable": None, "functional": None}


# --------------------------- coarse risk tier ---------------------------
def test_coarse_risk_tier_from_verb():
    assert derive_risk_tier(Operation("clusters", "list")) == RiskTier.R0
    assert derive_risk_tier(Operation("clusters", "create")) == RiskTier.R1
    assert derive_risk_tier(Operation("clusters", "delete")) == RiskTier.R2
    assert derive_risk_tier(Operation("workspaces", "delete")) == RiskTier.R3  # blocklisted floor


def test_one_confirm_tier_only_for_mutating_targets():
    create = Operation("clusters", "create")
    assert needs_tier_confirmation(create, SafetyClass.SANDBOX)
    assert needs_tier_confirmation(create, SafetyClass.PROD_FOR_US)
    assert not needs_tier_confirmation(create, SafetyClass.READ_ONLY)
    # a read op never needs confirmation, even on a mutating target
    assert not needs_tier_confirmation(Operation("clusters", "list"), SafetyClass.SANDBOX)


def test_confirmed_tier_can_only_raise_the_coarse_guess():
    create = Operation("clusters", "create")  # coarse R1
    assert effective_risk_tier(create, RiskTier.R2) == RiskTier.R2   # human raised it
    assert effective_risk_tier(create, RiskTier.R0) == RiskTier.R1   # can't lower below floor
    assert effective_risk_tier(create, None) == RiskTier.R1
