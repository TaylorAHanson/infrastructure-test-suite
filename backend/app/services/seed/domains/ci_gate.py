"""CI Gate (issue #98): the deterministic-only, read-only posture gate for
the CI trigger, reworked (#107) from an enumerated Test-key list into a
Suite the registry composes.

This module contributes no Checks or Tests of its own, so it declares
``compose_contribution`` instead of a static ``CONTRIBUTION`` — the
registry's other hook (:func:`app.services.seed.registry.discover_contribution
s`) for a domain whose own Contribution can only be computed *from* every
other domain's. It collects every discovered domain's ``ci_gate_tests`` — the
opt-in seam a domain module uses to volunteer a Test it owns as deterministic
and read-only (see :mod:`app.services.seed.contribution`) — and, before
trusting that opt-in, re-derives each member Check's evaluator and risk tier
straight from its spec via the same ``build_check_fields`` path
:func:`app.services.seed.build.make_check` uses at seed time. Only a Test
whose every member re-derives to ``Evaluator.DETERMINISTIC`` and
``RiskTier.R0`` survives into the gate, so a domain that mis-tags an agentic
or mutating Check into ``ci_gate_tests`` can't leak it through, and a
brand-new posture domain that volunteers its own deterministic R0 Test is
picked up automatically — this module never needs an edit when one lands.
"""
from __future__ import annotations

from app.core.enums import Evaluator, RiskTier
from app.schemas.schemas import CheckCreate
from app.services.authoring import build_check_fields
from app.services.seed.contribution import Contribution, SuiteSpec


def _is_deterministic_r0(check_spec: dict) -> bool:
    fields = build_check_fields(CheckCreate(**check_spec))
    return fields["evaluator"] == Evaluator.DETERMINISTIC and fields["risk_tier"] == RiskTier.R0.value


def _compose_test_keys(contributions: list[Contribution]) -> list[str]:
    checks = {key: spec for contribution in contributions for key, spec in contribution.check_specs}
    tests = {key: spec for contribution in contributions for key, spec in contribution.tests.items()}

    keys: list[str] = []
    for contribution in contributions:
        for key in contribution.ci_gate_tests:
            members = tests[key].members
            if all(_is_deterministic_r0(checks[check_key]) for check_key, _ in members):
                keys.append(key)
    return keys


def compose_contribution(contributions: list[Contribution]) -> Contribution:
    """Build the CI Gate Suite from every other domain's ``ci_gate_tests``.

    ``contributions`` is the full declarative set the registry has already
    discovered — this module isn't in it, since it never declares a static
    ``CONTRIBUTION`` of its own.
    """
    return Contribution(
        suites=[
            SuiteSpec(
                "CI Gate",
                "Deterministic-only, read-only posture gate for the CI trigger: every "
                "deterministic R0 Test a posture domain module volunteers — never a "
                "judgment read, never a mutation.",
                _compose_test_keys(contributions),
                deterministic_only=True,
                tags=["seeded", "ci"],
            ),
        ],
    )
