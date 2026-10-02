"""Contribution: the shape a domain module hands to the seed registry (#106).

Any module dropped into :mod:`app.services.seed.domains` that exposes a
module-level ``CONTRIBUTION: Contribution`` is picked up automatically by
:func:`app.services.seed.registry.discover_contributions` — no shared file
needs an edit to register a new domain. ``seed_all()`` then merges every
discovered Contribution's Tests/Suites into one catalog, so a
``SuiteSpec.test_keys`` entry may name a key minted by a *different* domain
module than the one declaring the Suite — resolution happens against the merged
whole, not each module's own slice.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TestSpec:
    """One Test: a Python script whose ``with check(...)`` blocks are its Checks."""

    name: str
    description: str
    script: str
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SuiteSpec:
    """One Suite: a named grouping of Test keys, which may span other modules."""

    name: str
    description: str
    test_keys: list[str]
    tags: list[str] | None = None


@dataclass(frozen=True)
class Contribution:
    """A domain module's slice of the seed catalog.

    ``ci_gate_tests`` is the CI Gate's opt-in seam (#98): a domain module that
    owns a deterministic, read-only (R0) Test lists its own key(s) here to
    volunteer for the CI Gate. The ``ci_gate`` domain module composes the
    gate's membership by collecting every discovered contribution's
    ``ci_gate_tests`` and re-deriving each Test's checks from its script's
    analysis before trusting the opt-in — so a domain never needs to edit
    ``ci_gate`` itself to join (or be kept out of) the gate.
    """

    tests: dict[str, TestSpec] = field(default_factory=dict)
    suites: list[SuiteSpec] = field(default_factory=list)
    ci_gate_tests: list[str] = field(default_factory=list)
