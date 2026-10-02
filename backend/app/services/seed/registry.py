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

The one-module-per-slice convention (#108, ADR-0022) is enforced here, not
just documented: a Test key contributed by two different modules
raises :class:`SeedKeyCollision` rather than silently clobbering — the merge
``seed_all()`` performs (dict keyed by these same strings) would otherwise
let the second module's row overwrite the first with no error at all.
:func:`domain_shape_leaked` is the other half of the guard (see
``tests/test_seed_module_convention.py``): a shared file that grows its own
``CONTRIBUTION``/``compose_contribution`` — the exact shape a domain module
uses — is the same trampling this package exists to prevent, just one level
up.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from app.services.seed.contribution import Contribution


class SeedKeyCollision(ValueError):
    """Two domain modules contributed the same Test key (#108, ADR-0022)."""


def domain_shape_leaked(module) -> bool:
    """True if ``module`` exposes the domain-module shape (#106): a
    ``CONTRIBUTION`` or a ``compose_contribution`` callable. Real domain
    modules live under ``app.services.seed.domains`` and are expected to
    return True here; any other module returning True has had a domain's
    contribution shape misplaced into it (#108, ADR-0022)."""
    return (
        isinstance(getattr(module, "CONTRIBUTION", None), Contribution)
        or callable(getattr(module, "compose_contribution", None))
    )


def _claim(keys, owners: dict[str, str], module_name: str, kind: str) -> None:
    """Record ``module_name`` as the owner of each key, raising :class:`SeedKeyCollision`
    if a different module already claimed one."""
    for key in keys:
        owner = owners.get(key)
        if owner is not None and owner != module_name:
            raise SeedKeyCollision(
                f"{kind} key {key!r} is contributed by both {owner!r} and {module_name!r} — "
                "give one of them a distinct key (#108, ADR-0022)."
            )
        owners[key] = module_name


def discover_contributions() -> list[Contribution]:
    """Import every module under ``domains`` and collect its Contribution(s).

    Discovery order is alphabetical by module name — deterministic, but not
    load-bearing: declarative contributions are merged before any
    cross-module reference (a Suite's test_keys) is resolved, so it doesn't
    matter which domain lands first. Composed contributions are appended after
    every declarative one is collected, so a composer sees the complete set.

    Raises :class:`SeedKeyCollision` if two modules (declarative or composed)
    mint the same Test key — see the module docstring.
    """
    from app.services.seed import domains

    contributions: list[Contribution] = []
    composers: list[tuple[str, Callable[[list[Contribution]], Contribution]]] = []
    test_owners: dict[str, str] = {}
    for module_info in sorted(pkgutil.iter_modules(domains.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{domains.__name__}.{module_info.name}")
        contribution = getattr(module, "CONTRIBUTION", None)
        if isinstance(contribution, Contribution):
            _claim(contribution.tests.keys(), test_owners, module_info.name, "Test")
            contributions.append(contribution)
        composer = getattr(module, "compose_contribution", None)
        if callable(composer):
            composers.append((module_info.name, composer))
    for module_name, composer in composers:
        composed = composer(contributions)
        _claim(composed.tests.keys(), test_owners, module_name, "Test")
        contributions.append(composed)
    return contributions
