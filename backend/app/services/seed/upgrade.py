"""Bring an already-seeded database up to the current catalog.

``seed_all()`` builds the catalog only into an empty database, so an install
seeded by an older release keeps the old Tests forever. This retires the Tests
and Suites earlier releases seeded, brings the seeded Tests and Suites nobody
has edited up to the bundled content, and adds whatever the current catalog has
that the database doesn't. It's idempotent: a database that's current is left
alone, and it runs on every startup, so it only ever touches rows the seeder
made and still owns.

* **Seeded vs. a person's.** A Test is the seeder's when nobody created it —
  every human/Assistant path records a ``test.create`` audit and a v1 snapshot
  under its own actor; the seeder records neither. A Suite is the seeder's when
  tagged ``seeded``. A seeded Check carries ``hints["seed_key"]``. A person's
  Test, Suite or Check is never retired, deleted, rewired or rewritten, even
  when it shares a seeded name.
* **Unmodified.** A seeded row is still the seeder's to update while its
  current version was written by the seeder (or is the never-snapshotted v1).
  Once a person edits it, the catalog stops touching it.

Retiring is in place, never a delete: run history points at the Test and its
Checks, so a retired Test is disabled, renamed with a ``(retired)`` suffix,
tagged ``retired`` and pulled out of every Suite. A retired Test is recognised
by an exact seeded name while it's still structured, or by a structured Test
holding a name the current catalog now gives to a script Test. Every change the
seeder makes to a Suite or Test is versioned and audited under the ``seed``
actor.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from app.models import AuditEvent, Check, EntityVersion, Suite, SuiteMember, Test, TestCheck
from app.services import versioning
from app.services.audit import CATALOG_UPGRADE, record_audit
from app.services.seed.build import SEED_KEY_HINT, make_check, make_suite, make_test
from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

logger = logging.getLogger(__name__)

RETIRED_TAG = "retired"
SEEDED_TAG = "seeded"
SEED_ACTOR = "seed"
#: Snapshot actors that mean "the seeder's content": its own updates, and the
#: ``system`` backfill of a seeded row's never-snapshotted version.
_SEED_AUTHORS = frozenset({SEED_ACTOR, "system"})

#: Structured Tests earlier releases seeded that the current catalog replaces.
RETIRED_TESTS = frozenset({
    "Consumer security posture",
    "Governance / Unity Catalog posture",
    "Identity & Access posture (read-only)",
    "Identity & Access posture (advisory)",
    "Processing compute posture",
    "Producer ingestion posture",
    "Job create/run/delete smoke",
    "SSO enforcement",
    "User isolation enforcement",
    "Exfiltration controls",
    "Token hygiene",
    "Workspace-admin count (advisory)",
    "Producer ingestion access",
    "Processing layer access",
})

#: Suites earlier releases seeded; their members are retired or regrouped.
RETIRED_SUITES = frozenset({
    "Consumer Suite",
    "Governance / Unity Catalog Suite",
    "Identity & Access Suite",
    "Processing Suite",
    "Producer Suite",
    "Sandbox Functional",
    "Security Posture Suite",
})

#: Suites whose membership the catalog owns outright, so it's reset on upgrade.
_OWNED_SUITES = frozenset({"CI Gate"})


def is_retired(test: Test) -> bool:
    return RETIRED_TAG in (test.tags or [])


class _Ownership:
    """Which catalog rows the seeder made and still owns.

    Read from the append-only audit log and the version history — the only
    records an install seeded by any earlier release is guaranteed to have.
    """

    def __init__(self, session: Session):
        self.session = session
        self._user_tests = set(session.exec(
            select(AuditEvent.entity_id).where(AuditEvent.action == "test.create")).all())
        self._user_tests |= set(session.exec(
            select(EntityVersion.entity_id).where(
                EntityVersion.entity_type == "test", EntityVersion.version == 1,
                EntityVersion.actor.not_in(_SEED_AUTHORS))).all())  # type: ignore[attr-defined]
        self._user_checks = set(session.exec(
            select(AuditEvent.entity_id).where(AuditEvent.action == "check.create")).all())
        self.deleted_suite_names = {
            (event.before or {}).get("name") for event in session.exec(
                select(AuditEvent).where(AuditEvent.action == "suite.delete",
                                         AuditEvent.actor != SEED_ACTOR)).all()}

    def seeded_test(self, test: Test) -> bool:
        return test.id not in self._user_tests

    def user_check(self, check: Check) -> bool:
        return check.id in self._user_checks

    @staticmethod
    def seeded_suite(suite: Suite) -> bool:
        return SEEDED_TAG in (suite.tags or [])

    def unmodified(self, entity_type: str, entity) -> bool:  # noqa: ANN001
        snap = versioning.get_version(self.session, entity_type, entity.id, entity.version)
        return entity.version == 1 if snap is None else snap.actor in _SEED_AUTHORS


def _retire_test(session: Session, test: Test, keep_check_ids: set[str]) -> None:
    test.enabled = False
    test.tags = [*(test.tags or []), RETIRED_TAG]
    if not test.name.endswith(" (retired)"):
        test.name = f"{test.name} (retired)"
    session.add(test)
    for member in session.exec(select(SuiteMember).where(SuiteMember.test_id == test.id)).all():
        session.delete(member)
    for link in session.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all():
        check = session.get(Check, link.check_id)
        if check is not None and check.id not in keep_check_ids:
            check.enabled = False
            session.add(check)


def _member_ids(session: Session, suite: Suite) -> list[str]:
    return list(session.exec(
        select(SuiteMember.test_id).where(SuiteMember.suite_id == suite.id)
        .order_by(SuiteMember.sort_order)  # type: ignore[arg-type]
    ).all())


def _suite_out(session: Session, suite: Suite) -> dict:
    return {**suite.model_dump(mode="json"), "test_ids": _member_ids(session, suite)}


def _set_members(session: Session, suite: Suite, test_ids: list[str]) -> None:
    for member in session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all():
        session.delete(member)
    session.flush()
    for i, test_id in enumerate(test_ids):
        session.add(SuiteMember(suite_id=suite.id, test_id=test_id, sort_order=i))


def _sync_suite(session: Session, suite: Suite, spec: SuiteSpec, tests: list[Test], *,
                owned: bool) -> bool:
    """Bring an unmodified seeded Suite up to its spec as a new, audited version.

    An owned Suite (the CI Gate) is reset to exactly the spec; any other gains
    the spec's Tests it's missing. Returns whether anything changed.
    """
    current = _member_ids(session, suite)
    wanted = [t.id for t in tests]
    members = wanted if owned else current + [tid for tid in wanted if tid not in current]
    fields = {"description": spec.description, "deterministic_only": spec.deterministic_only} if owned else {}
    changed = {k: v for k, v in fields.items() if getattr(suite, k) != v}
    if members == current and not changed:
        return False

    before = _suite_out(session, suite)
    for key, value in changed.items():
        setattr(suite, key, value)
    _set_members(session, suite, members)
    suite.version += 1
    session.add(suite)
    session.commit()
    session.refresh(suite)
    after = _suite_out(session, suite)
    versioning.snapshot(session, "suite", suite.id, suite.version, after, SEED_ACTOR)
    record_audit(session, actor=SEED_ACTOR, actor_kind="system", action="suite.update",
                 entity_type="suite", entity_id=suite.id,
                 summary=f"Catalog upgrade updated seeded suite {suite.name} to the bundled catalog",
                 before=before, after=after)
    return True


def _differs(test: Test, spec: TestSpec) -> bool:
    return (test.script != spec.script or test.description != spec.description
            or list(test.tags or []) != list(spec.tags))


def _renamed_seeded_tests(session: Session, seeded: list[Test], names: set[str]) -> dict[str, Test]:
    """Seeded Tests a person renamed, keyed by the bundled name they started with.

    Renaming is an edit, so the original seeded content was backfilled as v1.
    """
    unmatched = {t.id: t for t in seeded if t.name not in names}
    if not unmatched:
        return {}
    found: dict[str, Test] = {}
    for entity_id, snapshot in session.exec(
        select(EntityVersion.entity_id, EntityVersion.snapshot).where(
            EntityVersion.entity_type == "test", EntityVersion.version == 1,
            EntityVersion.entity_id.in_(sorted(unmatched)))  # type: ignore[attr-defined]
    ).all():
        original = (snapshot or {}).get("name")
        if original in names:
            found.setdefault(original, unmatched[entity_id])
    return found


def upgrade_catalog(session: Session, contributions: list[Contribution]) -> dict:
    """Retire superseded seeded rows, update unmodified ones, add what's missing."""
    owner = _Ownership(session)
    specs = {key: spec for c in contributions for key, spec in c.tests.items()}
    spec_names = {spec.name for spec in specs.values()}
    script_names = {spec.name for spec in specs.values() if spec.script}
    live = [t for t in session.exec(select(Test)).all() if not is_retired(t)]

    stale = [t for t in live if not t.script and owner.seeded_test(t)
             and (t.name in RETIRED_TESTS or t.name in script_names)]
    stale_ids = {t.id for t in stale}
    keep_check_ids = {link.check_id for link in session.exec(select(TestCheck)).all()
                      if link.test_id not in stale_ids}
    for test in stale:
        _retire_test(session, test, keep_check_ids)

    retired_suites: list[str] = []
    for suite in session.exec(select(Suite)).all():
        if (suite.name in RETIRED_SUITES and owner.seeded_suite(suite)
                and owner.unmodified("suite", suite)):
            before = _suite_out(session, suite)
            for member in session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all():
                session.delete(member)
            session.delete(suite)
            record_audit(session, actor=SEED_ACTOR, actor_kind="system", action="suite.delete",
                         entity_type="suite", entity_id=suite.id,
                         summary=f"Catalog upgrade removed retired seeded suite {suite.name}",
                         before=before, commit=False)
            retired_suites.append(suite.name)
    session.commit()

    all_seeded = [t for t in session.exec(select(Test)).all() if owner.seeded_test(t)]
    seeded_tests = [t for t in all_seeded if not is_retired(t)]
    by_name = {t.name: t for t in seeded_tests}
    for name, test in _renamed_seeded_tests(session, seeded_tests, spec_names - set(by_name)).items():
        by_name.setdefault(name, test)

    seeded_ids = {t.id for t in all_seeded}
    seeded_links = {link.check_id for link in session.exec(select(TestCheck)).all()
                    if link.test_id in seeded_ids}
    candidates = [c for c in session.exec(select(Check)).all() if c.enabled and not owner.user_check(c)]

    def seeded_check(check_key: str, display_name: str) -> Optional[Check]:
        for check in candidates:
            if (check.hints or {}).get(SEED_KEY_HINT) == check_key:
                return check
        # Installs seeded before checks carried their key: the seeder's own
        # Check of that name is one a seeded Test already uses.
        for check in candidates:
            if check.display_name == display_name and check.id in seeded_links:
                return check
        return None

    check_specs = {key: spec for c in contributions for key, spec in c.check_specs}
    check_tags = {key: c.check_tags for c in contributions for key, _ in c.check_specs}

    from app.services.script_tests import save_script

    tests: dict[str, Test] = {}
    added_tests = 0
    updated_tests: list[str] = []
    for key, spec in specs.items():
        existing = by_name.get(spec.name)
        if existing is not None:
            tests[key] = existing
            if (spec.script and existing.script and _differs(existing, spec)
                    and owner.unmodified("test", existing)):
                save_script(session, existing, spec.script, SEED_ACTOR,
                            description=spec.description, tags=list(spec.tags))
                updated_tests.append(existing.name)
            continue
        checks: dict[str, Check] = {}
        for check_key, _ in spec.members:
            check = seeded_check(check_key, check_specs[check_key]["display_name"])
            if check is None:
                check = make_check(check_specs[check_key], tags=check_tags.get(check_key) or None,
                                   seed_key=check_key)
                session.add(check)
                session.commit()
                session.refresh(check)
                candidates.append(check)
            checks[check_key] = check
        tests[key] = make_test(session, checks, spec)
        added_tests += 1

    seeded_suites: dict[str, Suite] = {}
    for suite in session.exec(select(Suite)).all():
        if owner.seeded_suite(suite):
            seeded_suites.setdefault(suite.name, suite)
    suite_specs = [s for c in contributions for s in c.suites]
    suite_specs.append(SuiteSpec("Full Regression", "Every seeded Test.", list(specs)))
    added_suites = 0
    updated_suites: list[str] = []
    for suite_spec in suite_specs:
        members = [tests[k] for k in suite_spec.test_keys]
        suite = seeded_suites.get(suite_spec.name)
        if suite is None:
            if suite_spec.name not in owner.deleted_suite_names:
                make_suite(session, tests, suite_spec)
                added_suites += 1
        elif owner.unmodified("suite", suite) and _sync_suite(
                session, suite, suite_spec, members, owned=suite_spec.name in _OWNED_SUITES):
            updated_suites.append(suite.name)
    session.commit()

    summary = {"retired_tests": len(stale), "retired_suites": len(retired_suites),
               "added_tests": added_tests, "added_suites": added_suites,
               "updated_tests": len(updated_tests), "updated_suites": len(updated_suites)}
    if any(summary.values()):
        record_audit(session, actor=SEED_ACTOR, actor_kind="system", action=CATALOG_UPGRADE,
                     entity_type="catalog", summary=(
                         f"Catalog upgrade: retired {len(stale)} Tests and {len(retired_suites)} Suites; "
                         f"added {added_tests} Tests and {added_suites} Suites; "
                         f"updated {len(updated_tests)} Tests and {len(updated_suites)} Suites."),
                     after={**summary, "retired_suite_names": retired_suites,
                            "updated_test_names": updated_tests,
                            "updated_suite_names": updated_suites})
        logger.info("Catalog upgraded: %s", summary)
    return summary
