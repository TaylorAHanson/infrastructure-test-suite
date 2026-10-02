"""Guard tests for the one-module-per-slice seed convention (issue #108).

#106/#107 split the monolithic seed catalog into `app/services/seed/domains/`
— one module per posture domain, auto-discovered by `registry.discover_
contributions()`, so a new domain is a single new file with zero edits to any
shared file. That convention is only real if something enforces it. Two
failure modes can quietly reintroduce the "one file everyone edits" trampling
this refactor exists to prevent:

1. Two domain modules mint the same Test key — silently colliding when
   `seed_all()` merges every contribution's dict, so the second write clobbers
   the first with no error (see `app.services.seed.registry.discover_
   contributions`, `app.services.seed.__init__.seed_all`).
2. A contributor drops a domain module's shape (`CONTRIBUTION` /
   `compose_contribution`) into a *shared* file instead of a new file under
   `domains/` — reintroducing a file everyone edits.

These are structural/convention tests, not catalog-shape tests (contrast
`tests/test_seed.py`'s header) — asserting seed-internal structure is exactly
the point here, since the structure itself is what's being locked in
(ADR-0022).
"""
from __future__ import annotations

import importlib
import types

import pytest

from app.services.seed import contribution as contribution_module
from app.services.seed import registry
from app.services.seed.contribution import Contribution


def _spec(name: str) -> contribution_module.TestSpec:
    return contribution_module.TestSpec(name, "desc", 'with check("ok"):\n    assert True\n')


def _fake_module_info(name: str):
    info = types.SimpleNamespace()
    info.name = name
    return info


def _fake_domain_module(name: str, contribution: Contribution | None = None,
                        composer=None) -> types.ModuleType:
    module = types.ModuleType(name)
    if contribution is not None:
        module.CONTRIBUTION = contribution
    if composer is not None:
        module.compose_contribution = composer
    return module


def _patch_domains(monkeypatch, modules_by_name: dict[str, types.ModuleType]) -> None:
    monkeypatch.setattr(
        registry.pkgutil, "iter_modules",
        lambda path: [_fake_module_info(name) for name in modules_by_name],
    )
    monkeypatch.setattr(
        registry.importlib, "import_module",
        lambda dotted: modules_by_name[dotted.rsplit(".", 1)[-1]],
    )


# ---------------------------------------------------------------------------
# 1. Cross-module key collisions
# ---------------------------------------------------------------------------

def test_discover_contributions_rejects_cross_module_test_key_collision(monkeypatch):
    spec = _spec("Some Test")
    fake_a = _fake_domain_module("fake_a", Contribution(tests={"dup_test": spec}))
    fake_b = _fake_domain_module("fake_b", Contribution(tests={"dup_test": spec}))
    _patch_domains(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})

    with pytest.raises(registry.SeedKeyCollision) as exc:
        registry.discover_contributions()

    message = str(exc.value)
    assert "dup_test" in message
    assert "fake_a" in message and "fake_b" in message


def test_discover_contributions_allows_disjoint_keys_across_modules(monkeypatch):
    fake_a = _fake_domain_module("fake_a", Contribution(tests={"a_test": _spec("A's test")}))
    fake_b = _fake_domain_module("fake_b", Contribution(tests={"b_test": _spec("B's test")}))
    _patch_domains(monkeypatch, {"fake_a": fake_a, "fake_b": fake_b})

    contributions = registry.discover_contributions()
    keys = {key for contribution in contributions for key in contribution.tests}
    assert {"a_test", "b_test"} <= keys


def test_real_domain_modules_have_no_key_collisions():
    """The regression net: this is what actually protects the real catalog —
    if a future domain module reuses another's key, this call raises."""
    registry.discover_contributions()


# ---------------------------------------------------------------------------
# 2. Domain shape must not leak into a shared file
# ---------------------------------------------------------------------------

_SHARED_SEED_MODULES = [
    "app.services.seed",
    "app.services.seed.build",
    "app.services.seed.contribution",
    "app.services.seed.registry",
]


def test_domain_shape_leak_detector_flags_a_misplaced_contribution():
    """Demonstrates the detector actually fires: a module carrying CONTRIBUTION
    or compose_contribution outside `domains/` is exactly the mistake this
    convention forbids."""
    leaked = _fake_domain_module(
        "app.services.seed.build",
        Contribution(tests={"oops": _spec("Oops")}),
    )
    assert registry.domain_shape_leaked(leaked)

    leaked_composer = _fake_domain_module(
        "app.services.seed.registry", composer=lambda contributions: Contribution())
    assert registry.domain_shape_leaked(leaked_composer)


def test_domain_shape_leak_detector_passes_an_ordinary_shared_module():
    ordinary = types.ModuleType("app.services.seed.build")
    assert not registry.domain_shape_leaked(ordinary)


@pytest.mark.parametrize("module_name", _SHARED_SEED_MODULES)
def test_shared_seed_modules_declare_no_domain_contribution(module_name):
    module = importlib.import_module(module_name)
    assert not registry.domain_shape_leaked(module), (
        f"{module_name} declares CONTRIBUTION/compose_contribution — that shape belongs in a "
        f"new module under app.services.seed.domains, not a shared file (ADR-0022)"
    )


# ---------------------------------------------------------------------------
# 3. A new domain needs a single new file, no shared-file edits
# ---------------------------------------------------------------------------

def test_a_new_domain_module_is_discovered_with_zero_shared_file_changes(monkeypatch):
    """Simulates "drop a new file in domains/": discover_contributions() (real,
    unmodified) picks it up purely via pkgutil/importlib, so registering a new
    domain never requires touching build.py/registry.py/__init__.py."""
    new_domain = _fake_domain_module("new_posture_domain", Contribution(
        tests={"new_posture_test": _spec("New posture test")},
    ))
    _patch_domains(monkeypatch, {"new_posture_domain": new_domain})

    contributions = registry.discover_contributions()
    keys = {key for contribution in contributions for key in contribution.tests}
    assert "new_posture_test" in keys
