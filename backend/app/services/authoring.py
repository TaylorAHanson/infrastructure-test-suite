"""Turning author input into a valid Check (compile + derive + validate).

This is the deterministic write-path plumbing shared by the no-code GUI and the
Assistant (Spec 4): given the plain-text assertion + the open-SDK operation, it

* **compiles** the assertion to a deterministic predicate, or flags it agentic
  (:mod:`app.services.assertion_compiler`);
* **derives** the coarse risk tier from the operation's verb (:mod:`app.services.risk`);
* **refuses** a blocklisted operation — the R3-never floor (:mod:`app.executors.registry`).

The Assistant's LLM only *drafts* the plain text; this module — not the model —
decides the evaluator, so the determinism guarantee stays honest.
"""
from __future__ import annotations

from app.core.operations import Operation
from app.executors.registry import validate_operation
from app.schemas.schemas import CheckCreate, CheckUpdate
from app.services.assertion_compiler import compile_assertion
from app.services.risk import derive_risk_tier


def build_check_fields(payload: CheckCreate) -> dict:
    """Compile + derive + validate a CheckCreate into Check column values.

    Raises :class:`app.executors.registry.OperationBlocked` if the operation is
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
