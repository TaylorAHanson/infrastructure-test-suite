"""CI Gate (issue #98): the deterministic-only, read-only posture gate for
the CI trigger, reworked (#107) from an enumerated Test-key list into a
Suite the registry composes.

This module contributes no Tests of its own, so it declares
``compose_contribution`` instead of a static ``CONTRIBUTION`` — the
registry's other hook (:func:`app.services.seed.registry.discover_contribution
s`) for a domain whose own Contribution can only be computed *from* every
other domain's. It collects every discovered domain's ``ci_gate_tests`` — the
opt-in seam a domain module uses to volunteer a Test it owns as deterministic
and read-only (see :mod:`app.services.seed.contribution`) — and, before
trusting that opt-in, re-derives each Test's checks from its script's
analysis. Only a valid, read-only (R0) script with no advisory check survives
into the gate, so a domain that mis-tags an advisory or mutating Test into
``ci_gate_tests`` can't leak it through, and a brand-new posture domain that
volunteers its own read-only Test is picked up automatically — this module
never needs an edit when one lands.
"""
from __future__ import annotations

from app.core.enums import RiskTier
from app.script_runtime.analysis import analyze
from app.services.seed.contribution import Contribution, SuiteSpec


def _script_gates(script: str) -> bool:
    """A script can gate when it's valid, read-only, and has no advisory checks."""
    analysis = analyze(script)
    return (analysis["ok"] and bool(analysis["checks"])
            and analysis["max_risk_tier"] == RiskTier.R0.value
            and not any(c.get("advisory") for c in analysis["checks"]))


def _compose_test_keys(contributions: list[Contribution]) -> list[str]:
    tests = {key: spec for contribution in contributions for key, spec in contribution.tests.items()}
    return [key for contribution in contributions for key in contribution.ci_gate_tests
            if _script_gates(tests[key].script)]


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
                "Run after every apply: read-only health checks against the canary objects "
                "terraform provisions — identity, metastore, SQL, Unity Catalog storage and "
                "grants. Never a judgment read, never a mutation.",
                _compose_test_keys(contributions),
                tags=["seeded", "ci"],
            ),
        ],
    )
