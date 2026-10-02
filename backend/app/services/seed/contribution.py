"""Contribution: the shape a domain module hands to the seed registry (#106).

Any module dropped into :mod:`app.services.seed.domains` that exposes a
module-level ``CONTRIBUTION: Contribution`` is picked up automatically by
:func:`app.services.seed.registry.discover_contributions` — no shared file
needs an edit to register a new domain. ``seed_all()`` then merges every
discovered Contribution's Checks/Tests/Suites into one catalog, so a
``TestSpec.members`` / ``SuiteSpec.test_keys`` / counterfactual counterpart may
name a key minted by a *different* domain module than the one declaring the
reference — resolution happens against the merged whole, not each module's
own slice.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TestSpec:
    """One Test: a Python script, or (legacy) a bundle of structured Check references.

    A script Test sets ``script``; its checks come from the script's own
    ``with check(...)`` blocks, so ``members`` stays empty. A structured Test's
    ``members`` is a list of ``(check_key, kind)`` pairs, ``kind`` one of
    ``"assertion"`` or ``"counterfactual"``. A counterfactual's counterpart is
    resolved as the nearest preceding assertion-kind member — the positive
    Check it negates — at build time, once every domain's Checks are merged.
    """

    name: str
    description: str
    members: list[tuple[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    script: str | None = None


@dataclass(frozen=True)
class SuiteSpec:
    """One Suite: a named grouping of Test keys, which may span other modules."""

    name: str
    description: str
    test_keys: list[str]
    deterministic_only: bool = False
    tags: list[str] | None = None


@dataclass(frozen=True)
class Contribution:
    """A domain module's slice of the seed catalog.

    ``ci_gate_tests`` is the CI Gate's opt-in seam (#98): a domain module that
    owns a deterministic, read-only (R0) Test lists its own key(s) here to
    volunteer for the CI Gate. The ``ci_gate`` domain module composes the
    gate's membership by collecting every discovered contribution's
    ``ci_gate_tests`` and re-deriving each Test's checks (from its script's
    analysis, or each member Check's spec) before trusting the opt-in — so a
    domain never needs to edit ``ci_gate`` itself to join (or be kept out of)
    the gate.
    """

    check_specs: list[tuple[str, dict]] = field(default_factory=list)
    check_tags: list[str] = field(default_factory=list)
    tests: dict[str, TestSpec] = field(default_factory=dict)
    suites: list[SuiteSpec] = field(default_factory=list)
    worked_examples: list[dict] = field(default_factory=list)
    ci_gate_tests: list[str] = field(default_factory=list)
