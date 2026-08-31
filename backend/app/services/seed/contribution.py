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
    """One Test: a target-agnostic bundle of Check references.

    ``members`` is a list of ``(check_key, kind)`` pairs, ``kind`` one of
    ``"assertion"`` or ``"counterfactual"``. A counterfactual's counterpart is
    resolved as the nearest preceding assertion-kind member — the positive
    Check it negates — at build time, once every domain's Checks are merged.
    """

    name: str
    description: str
    members: list[tuple[str, str]]
    tags: list[str] = field(default_factory=list)


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
    """A domain module's slice of the seed catalog."""

    check_specs: list[tuple[str, dict]] = field(default_factory=list)
    check_tags: list[str] = field(default_factory=list)
    tests: dict[str, TestSpec] = field(default_factory=dict)
    suites: list[SuiteSpec] = field(default_factory=list)
    worked_examples: list[dict] = field(default_factory=list)
