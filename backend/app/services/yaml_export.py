"""YAML export of a Test/Suite — a review/diff/portability format only.

The app remains the source of truth (Lakebase-authoritative, ADR-0001); this
renders a Test or Suite as YAML so it can be reviewed or diffed in a PR. There
is deliberately **no** authoritative YAML import that writes state back.
"""
from __future__ import annotations

import yaml
from sqlmodel import Session, select

from app.models import Check, Suite, SuiteMember, Test, TestCheck


def _check_dict(check: Check) -> dict:
    return {
        "id": check.id,
        "display_name": check.display_name,
        "description": check.description,
        "operation": check.operation,
        "handler_key": check.handler_key,
        "assertion": check.assertion,
        "expectation": check.expectation.value,
        "expected_stage": check.expected_stage,
        "evaluator": check.evaluator.value,
        "risk_tier": check.risk_tier,
    }


def test_to_dict(session: Session, test: Test) -> dict:
    members = session.exec(
        select(TestCheck).where(TestCheck.test_id == test.id).order_by(TestCheck.sort_order)  # type: ignore[arg-type]
    ).all()
    checks = []
    for m in members:
        check = session.get(Check, m.check_id)
        if not check:
            continue
        entry = _check_dict(check)
        entry["kind"] = m.kind
        if m.run_as_role:
            entry["run_as_role"] = m.run_as_role
        checks.append(entry)
    return {
        "test": {
            "id": test.id,
            "name": test.name,
            "description": test.description,
            "tags": test.tags,
            "run_as_role": test.run_as_role,
            "version": test.version,
            "checks": checks,
        }
    }


def test_to_yaml(session: Session, test: Test) -> str:
    return yaml.safe_dump(test_to_dict(session, test), sort_keys=False)


def suite_to_dict(session: Session, suite: Suite) -> dict:
    members = session.exec(
        select(SuiteMember).where(SuiteMember.suite_id == suite.id).order_by(SuiteMember.sort_order)  # type: ignore[arg-type]
    ).all()
    tests = []
    for m in members:
        test = session.get(Test, m.test_id)
        if test:
            tests.append(test_to_dict(session, test)["test"])
    return {
        "suite": {
            "id": suite.id,
            "name": suite.name,
            "description": suite.description,
            "tags": suite.tags,
            "deterministic_only": suite.deterministic_only,
            "version": suite.version,
            "tests": tests,
        }
    }


def suite_to_yaml(session: Session, suite: Suite) -> str:
    return yaml.safe_dump(suite_to_dict(session, suite), sort_keys=False)
