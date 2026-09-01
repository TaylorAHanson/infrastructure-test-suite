"""Auto-discovering domain registry (#106).

Walks :mod:`app.services.seed.domains` and imports every module it finds
there. Two kinds of module are collected:

* a module that exposes a module-level ``CONTRIBUTION`` (a
  :class:`~app.services.seed.contribution.Contribution`) — the ordinary,
  declarative shape every posture domain uses;
* a module that instead exposes a ``compose_contribution(contributions)``
  callable — for a domain whose own Contribution can only be computed *from*
  every other domain's (the CI Gate, #98: "every deterministic R0 posture
  Test", not an enumerated list). Composers run after every declarative
  Contribution is collected, each handed that full list, so a composer never
  needs special-casing here by name — dropping a new domain module into
  ``domains`` (declarative or composed) is enough to register it, no shared
  file (this one included) needs an edit.
"""
from __future__ import annotations

import importlib
import pkgutil

from app.services.seed.contribution import Contribution


def discover_contributions() -> list[Contribution]:
    """Import every module under ``domains`` and collect its Contribution(s).

    Discovery order is alphabetical by module name — deterministic, but not
    load-bearing: declarative contributions are merged before any
    cross-module reference (a Test's members, a Suite's test_keys, a
    counterfactual's counterpart) is resolved, so it doesn't matter which
    domain lands first. Composed contributions are appended after every
    declarative one is collected, so a composer sees the complete set.
    """
    from app.services.seed import domains

    contributions: list[Contribution] = []
    composers = []
    for module_info in sorted(pkgutil.iter_modules(domains.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{domains.__name__}.{module_info.name}")
        contribution = getattr(module, "CONTRIBUTION", None)
        if isinstance(contribution, Contribution):
            contributions.append(contribution)
        composer = getattr(module, "compose_contribution", None)
        if callable(composer):
            composers.append(composer)
    contributions.extend(composer(contributions) for composer in composers)
    return contributions
