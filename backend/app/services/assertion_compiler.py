"""Plain-text assertion → deterministic structured predicate (ADR-0001/0008).

A validation author writes an assertion in **plain text** ("the query returns
at least one row", "HTTP status is 200", "a non-compliant cluster is denied").
This compiler turns that into a **deterministic structured predicate** where it
can, or **flags it as non-compilable** where it can't ("the warehouse came up
healthy-ish", "the error message is clear and actionable").

* Compiles  → ``evaluator = deterministic`` — same evidence, same verdict; may gate CI.
* Can't compile → ``evaluator = agentic`` — advisory only, never a gate (ADR-0008).

This module is the **deterministic** compile/flag seam only. The AI authoring
assistant (Spec 4) calls an LLM to *draft* the plain text; the LLM does not
decide the evaluator — this rule-based compiler does, so the determinism
guarantee is honest. Non-determinism never sneaks into the gating path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.core.enums import Evaluator, Expectation


@dataclass
class CompiledAssertion:
    """The result of compiling one plain-text assertion."""

    source_text: str
    compilable: bool
    evaluator: Evaluator
    expectation: Expectation = Expectation.PASS
    predicate: Optional[dict] = None
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "source_text": self.source_text,
            "compilable": self.compilable,
            "evaluator": self.evaluator.value,
            "expectation": self.expectation.value,
            "predicate": self.predicate,
            "reason": self.reason,
        }


# Words that signal a *fuzzy* judgement no structured predicate can capture.
# Their presence forces the agentic-advisory path even if a number is nearby.
_FUZZY_MARKERS = (
    "healthy-ish", "healthyish", "clear and actionable", "reasonable", "reasonably",
    "looks good", "look good", "seems", "roughly", "approximately", "about right",
    "should be fine", "sane", "sensible", "nice", "clean enough", "good enough",
    "acceptable", "readable", "well-formed-ish", "human-friendly", "makes sense",
)

# Denial / counterfactual language → expectation DENY.
_DENY_MARKERS = (
    "denied", "blocked", "forbidden", "rejected", "not allowed", "disallowed",
    "fails closed", "fail closed", "refused", "prevented", "permission error",
)

# Plain "it worked" language → a bare success predicate.
_SUCCESS_MARKERS = (
    "succeeds", "succeed", "is allowed", "allowed", "works", "reachable",
    "connects", "can connect", "returns successfully", "completes", "ok",
)

_COMPARE_RE = re.compile(
    r"([a-z_][a-z0-9_\.]*)\s*(==|=|>=|<=|>|<|is|equals?|of)\s*([0-9]+)",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(r"\b(?:http\s+)?status(?:\s*code)?\D{0,6}?([0-9]{3})\b", re.IGNORECASE)
_ATLEAST_RE = re.compile(r"at least\s+([0-9]+)\s+row", re.IGNORECASE)

_OP_MAP = {"==": "==", "=": "==", "is": "==", "equal": "==", "equals": "==", "of": "==",
           ">=": ">=", "<=": "<=", ">": ">", "<": "<"}


def compile_assertion(text: str) -> CompiledAssertion:
    """Compile ``text`` to a deterministic predicate, or flag it agentic."""
    raw = (text or "").strip()
    lowered = raw.lower()

    if not raw:
        return CompiledAssertion(raw, False, Evaluator.AGENTIC,
                                 reason="Empty assertion; nothing to compile.")

    # 1. Fuzzy language always routes to agentic-advisory, even near a number.
    if any(marker in lowered for marker in _FUZZY_MARKERS):
        return CompiledAssertion(
            raw, False, Evaluator.AGENTIC,
            reason="Contains a subjective/fuzzy judgement no deterministic predicate can capture.",
        )

    # 2. Counterfactual denial ("...is denied", "fails closed").
    if any(marker in lowered for marker in _DENY_MARKERS):
        return CompiledAssertion(
            raw, True, Evaluator.DETERMINISTIC, expectation=Expectation.DENY,
            predicate={"kind": "denied"},
            reason="Denial/counterfactual assertion: passes iff the operation was blocked.",
        )

    # 3. HTTP status ("status is 200", "returns HTTP 200").
    m = _STATUS_RE.search(raw)
    if m:
        return CompiledAssertion(
            raw, True, Evaluator.DETERMINISTIC,
            predicate={"kind": "compare", "field": "status_code", "op": "==", "value": int(m.group(1))},
            reason="Compiled to a status_code equality predicate.",
        )

    # 4. "at least N rows".
    m = _ATLEAST_RE.search(raw)
    if m:
        return CompiledAssertion(
            raw, True, Evaluator.DETERMINISTIC,
            predicate={"kind": "compare", "field": "row_count", "op": ">=", "value": int(m.group(1))},
            reason="Compiled to a row_count threshold predicate.",
        )

    # 5. "returns rows" / "non-empty" / "at least one row".
    if ("at least one row" in lowered or "returns rows" in lowered or "return rows" in lowered
            or "non-empty" in lowered or "not empty" in lowered or "has rows" in lowered):
        return CompiledAssertion(
            raw, True, Evaluator.DETERMINISTIC,
            predicate={"kind": "compare", "field": "row_count", "op": ">=", "value": 1},
            reason="Compiled to a non-empty row_count predicate.",
        )

    # 6. Reachability: "returns ... without error" — the safe form for a
    # resource that can legitimately be empty (e.g. a clean workspace with zero
    # pipelines/policies/scopes). Compiles to a bare success predicate so it
    # can still gate CI; it never asserts non-emptiness.
    if "without error" in lowered:
        return CompiledAssertion(
            raw, True, Evaluator.DETERMINISTIC,
            predicate={"kind": "ok"},
            reason="Compiled to a bare success predicate (reachability: returns without error).",
        )

    # 7. Generic "<field> <op> <number>" comparison.
    m = _COMPARE_RE.search(raw)
    if m:
        field_name, op_raw, value = m.group(1), m.group(2).lower(), int(m.group(3))
        op = _OP_MAP.get(op_raw, "==")
        return CompiledAssertion(
            raw, True, Evaluator.DETERMINISTIC,
            predicate={"kind": "compare", "field": field_name, "op": op, "value": value},
            reason=f"Compiled to a comparison predicate on '{field_name}'.",
        )

    # 8. Bare success language ("the query succeeds", "egress is allowed").
    if any(marker in lowered for marker in _SUCCESS_MARKERS):
        return CompiledAssertion(
            raw, True, Evaluator.DETERMINISTIC,
            predicate={"kind": "ok"},
            reason="Compiled to a bare success predicate (operation completed without failure).",
        )

    # 9. Otherwise: flag it. Never silently accept an uncompilable assertion.
    return CompiledAssertion(
        raw, False, Evaluator.AGENTIC,
        reason="No deterministic predicate matched; will run as agentic-advisory (never gates).",
    )


# ---------------------------------------------------------------------------
# Evaluating a compiled predicate over evidence (used by the run engine)
# ---------------------------------------------------------------------------
_COMPARATORS = {
    "==": lambda a, b: a == b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def evaluate_predicate(predicate: Optional[dict], evidence: dict, operation_succeeded: bool) -> bool:
    """Evaluate a compiled predicate over a Check's structured evidence.

    ``operation_succeeded`` is whether the OPERATE stage completed without a
    failure — the substrate the ``ok`` and ``denied`` predicates read.
    """
    if not predicate:
        return operation_succeeded
    kind = predicate.get("kind")
    if kind == "ok":
        return operation_succeeded
    if kind == "denied":
        return not operation_succeeded
    if kind == "compare":
        field_name = predicate.get("field", "")
        expected = predicate.get("value")
        actual = evidence.get(field_name)
        if actual is None:
            return False
        try:
            comparator = _COMPARATORS[predicate.get("op", "==")]
            return bool(comparator(actual, expected))
        except (TypeError, KeyError):
            return False
    return operation_succeeded
