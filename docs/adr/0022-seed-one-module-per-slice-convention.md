# ADR 0022 — Seed catalog: one module per domain/slice, enforced

- **Status:** Accepted (2026-09-01). Follows [#106](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/106) (auto-discovering registry) and [#107](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/107) (every real-catalog domain migrated to its own module); implemented by [#108](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/108).
- **Context effort:** Wayfinder map [#1](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/1) (rebuild of the validation platform).

## Context

The seed catalog (Spec [#91](https://github.com/databricks-field-eng/infrastructure-test-suite/issues/91)) started as one growing file that every posture domain's Checks/Tests/Suites were appended to — the exact shape that produces merge-conflict trampling once more than one person or agent is adding domains concurrently (as a `/tally-ho` swarm slice does). #106 fixed the mechanism: `app/services/seed/domains/` is walked by `registry.discover_contributions()`, and any module there exposing a module-level `CONTRIBUTION` (or a `compose_contribution(contributions)` callable, for a domain computed from every other domain's — see the CI Gate) is picked up with no shared file needing an edit. #107 then split the one remaining multi-domain module (`catalog.py`) into one file per domain (producer, processing, consumer, security_posture, identity_access, governance, sandbox_functional).

That gives every *domain* its own file. Nothing yet stops the trampling from recurring one level up: a Check or Test dropped directly into a *shared* file (`__init__.py`, `build.py`, `registry.py`, `contribution.py`) instead of a new `domains/*.py` module, or two domain modules independently choosing the same Check/Test key — which `seed_all()`'s dict-keyed merge (`app/services/seed/__init__.py`) would silently let clobber, with no error. Without something enforcing it, "one module per slice" is a convention a reviewer has to remember, not a rule CI checks.

## Decision

1. **One module per domain/slice, no shared seed file.** Every posture domain's Checks/Tests/Suites live in exactly one file under `app/services/seed/domains/`, declaring a module-level `CONTRIBUTION` (or, for a domain computed from every other domain's contributions, a `compose_contribution` callable). `__init__.py`, `build.py`, `registry.py`, and `contribution.py` are the only shared files, and they carry no domain content of their own — only the generic discovery/merge/build machinery, plus the two documented cross-cutting exceptions that aren't domain content (the "Full Regression" suite that unions every discovered Test, and the demo `Target` rows).
2. **Cross-module key collisions are a hard error, not a hope.** `registry.discover_contributions()` tracks which module claimed each Check/Test key as it merges contributions and raises `SeedKeyCollision` the moment two different modules claim the same one — before `seed_all()` ever gets a chance to silently let the second clobber the first.
3. **Domain shape leaking into a shared file is a guarded, testable condition.** `registry.domain_shape_leaked(module)` reports whether a module exposes the domain-module shape (`CONTRIBUTION` / `compose_contribution`). A parametrized guard test (`tests/test_seed_module_convention.py`) asserts every shared seed module returns `False`; a module returning `True` outside `domains/` is exactly the mistake this ADR forbids.
4. **The convention is demonstrated end to end.** The same guard test suite shows a fake domain module is discovered by `discover_contributions()` — unmodified — via `pkgutil`/`importlib` alone, so registering a new domain is a single new file with zero edits to any of the four shared files.

## Consequences

- **+** The "don't all edit one file" rule is now something CI enforces (a raised `SeedKeyCollision`, a failing guard test) rather than something a reviewer has to catch by eye — load-bearing for concurrent `/tally-ho` swarm slices each adding a domain in parallel.
- **+** A future posture domain (or any future domain-shaped catalog slice) is additive: one new file under `domains/`, auto-discovered, no PR touches `build.py`/`registry.py`/`contribution.py`/`__init__.py`.
- **−** The guard tests assert seed-*internal* structure (module shape, key ownership), which cuts against this repo's usual preference for asserting only observable catalog shape (`tests/test_seed.py`'s stated seam). That's a deliberate, scoped exception: the structure itself is the thing being locked in here, so there is no purely-observable substitute.
- **−** `SeedKeyCollision` only catches a *reused* key across modules; a module choosing a key that shadows nothing but is still poorly named is a review concern, not something automation can catch.

## Downstream

Builds directly on #106/#107's discovery/module-per-domain mechanism. A future domain module (or CI Gate–style composer) inherits the collision guard and the shape guard automatically — neither requires an opt-in.
