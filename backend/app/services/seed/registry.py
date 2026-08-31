"""Auto-discovering domain registry (#106).

Walks :mod:`app.services.seed.domains` and imports every module it finds
there; a module that exposes a module-level ``CONTRIBUTION`` (a
:class:`~app.services.seed.contribution.Contribution`) is collected. Dropping
a new domain module into that package is therefore enough to register it —
no shared file (this one included) needs an edit.
"""
from __future__ import annotations

import importlib
import pkgutil

from app.services.seed.contribution import Contribution


def discover_contributions() -> list[Contribution]:
    """Import every module under ``domains`` and collect its ``CONTRIBUTION``.

    Discovery order is alphabetical by module name — deterministic, but not
    load-bearing: contributions are merged before any cross-module reference
    (a Test's members, a Suite's test_keys, a counterfactual's counterpart)
    is resolved, so it doesn't matter which domain lands first.
    """
    from app.services.seed import domains

    contributions: list[Contribution] = []
    for module_info in sorted(pkgutil.iter_modules(domains.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{domains.__name__}.{module_info.name}")
        contribution = getattr(module, "CONTRIBUTION", None)
        if isinstance(contribution, Contribution):
            contributions.append(contribution)
    return contributions
