"""purge_legacy(): structured Tests and Checks go, with everything that points at
them, in one audited transaction; script Tests and their history stay.

Migration ``wf43legacypurge`` does the same on Postgres, so the two share the
tests that pin what is deleted. The database here enforces foreign keys (SQLite
doesn't by default; Postgres always does), so a delete in the wrong order fails.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, inspect
from sqlmodel import select

from app.core.enums import RunStatus, TestStatus
from app.models import (
    AuditEvent,
    Check,
    CheckRun,
    EntityVersion,
    RunRequest,
    StageOutcome,
    Suite,
    SuiteMember,
    Target,
    Test,
    TestCheck,
)
from app.services import legacy_purge
from app.services.legacy_purge import purge_legacy
from app.services.script_tests import create_script_test

SCRIPT = 'with check("the identity authenticates"):\n    assert w.current_user.me().active\n'
MIGRATION = Path(__file__).resolve().parent.parent / "backend/migrations/versions/wf43_legacy_purge.py"


def _migrate(s) -> dict:
    spec = importlib.util.spec_from_file_location("wf43_legacy_purge", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(s.connection())):
        module.upgrade()
    s.commit()
    audit = s.exec(select(AuditEvent).where(AuditEvent.action == "catalog.upgrade")).first()
    return {k: v for k, v in audit.after.items() if isinstance(v, int)} if audit else {}


PURGES = {"service": lambda s: purge_legacy(s, actor="seed"), "migration": _migrate}


@pytest.fixture()
def db(db_path):
    from app.db.session import create_all, get_engine, session_scope

    engine = get_engine()
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    engine.dispose()
    create_all()
    with session_scope() as s:
        assert s.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        yield s


def _structured(s, name: str, *check_names: str) -> tuple[Test, list[Check]]:
    test = Test(name=name, tags=["tutorial"])
    checks = [Check(display_name=n, handler_key="databricks_sdk",
                    operation={"service": "clusters", "method": "list"}) for n in check_names]
    s.add_all([test, *checks])
    s.commit()
    for i, check in enumerate(checks):
        s.add(TestCheck(test_id=test.id, check_id=check.id, sort_order=i,
                        kind="counterfactual" if i else "assertion",
                        counterpart_check_id=checks[0].id if i else None))
    s.add_all([EntityVersion(entity_type="test", entity_id=test.id, snapshot={"name": name}),
               *[EntityVersion(entity_type="check", entity_id=c.id, snapshot={}) for c in checks]])
    s.commit()
    return test, checks


def _script(s, name: str) -> tuple[Test, list[Check]]:
    test, _ = create_script_test(s, name=name, actor="alice", script=SCRIPT, status=TestStatus.ACTIVE)
    links = s.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all()
    return test, [s.get(Check, link.check_id) for link in links]


def _run(s, status: RunStatus, *results: tuple[Test, Check]) -> RunRequest:
    request = RunRequest(status=status)
    s.add(request)
    s.commit()
    for test, check in results:
        run = CheckRun(request_id=request.id, test_id=test.id, check_id=check.id, verdict=RunStatus.PASS)
        s.add(run)
        s.commit()
        s.add_all([StageOutcome(run_id=run.run_id, stage="CONNECT"),
                   StageOutcome(run_id=run.run_id, stage="OPERATE")])
    s.commit()
    return request


def _worked_examples(s, n: int) -> None:
    connection = s.connection()
    connection.exec_driver_sql("CREATE TABLE worked_examples (id VARCHAR PRIMARY KEY, intent VARCHAR)")
    for i in range(n):
        connection.exec_driver_sql(f"INSERT INTO worked_examples VALUES ('ex{i}', 'intent {i}')")
    s.commit()


def _has_worked_examples(s) -> bool:
    return inspect(s.connection()).has_table("worked_examples")


def _ids(s, model, column="id") -> set[str]:
    return set(s.exec(select(getattr(model, column))).all())


@pytest.fixture()
def install(db):
    """An install with both kinds of Test, their run history and the worked examples."""
    tutorial, (positive, denied) = _structured(db, "Cluster policy enforcement", "allowed", "denied")
    mine, (my_check,) = _structured(db, "My structured test", "mine")
    db.add(AuditEvent(action="test.create", entity_type="test", entity_id=mine.id, actor="bob"))
    script, (script_check,) = _script(db, "Workspace access")
    suite = Suite(name="Connectivity Suite", tags=["seeded"])
    target = Target(name="t")
    db.add_all([suite, target])
    db.commit()
    for i, test in enumerate((tutorial, mine, script)):
        db.add(SuiteMember(suite_id=suite.id, test_id=test.id, sort_order=i))
    db.add(EntityVersion(entity_type="suite", entity_id=suite.id, snapshot={}))
    db.commit()
    rows = {
        "legacy_only": _run(db, RunStatus.FAIL, (tutorial, positive), (tutorial, denied), (mine, my_check)),
        "mixed": _run(db, RunStatus.PASS, (tutorial, positive), (script, script_check)),
        "legacy_running": _run(db, RunStatus.RUNNING, (mine, my_check)),
        "script_only": _run(db, RunStatus.PASS, (script, script_check)),
    }
    _worked_examples(db, 3)
    return {"script": script, "script_check": script_check, "suite": suite, **rows}


@pytest.mark.parametrize("how", sorted(PURGES))
def test_it_deletes_exactly_the_legacy_rows_and_what_points_at_them(db, install, how):
    script, script_check, suite = install["script"], install["script_check"], install["suite"]
    script_versions = {v.id for v in db.exec(select(EntityVersion).where(
        EntityVersion.entity_id.in_([script.id, script_check.id, suite.id]))).all()}
    script_runs = {r.run_id for r in db.exec(select(CheckRun).where(CheckRun.test_id == script.id)).all()}

    counts = PURGES[how](db)

    assert counts == {"stage_outcomes": 10, "check_runs": 5, "run_requests": 1, "test_checks": 3,
                      "suite_members": 2, "entity_versions": 5, "tests": 2, "checks": 3,
                      "worked_examples": 3}
    assert _ids(db, Test) == {script.id}
    assert _ids(db, Check) == {script_check.id}
    assert {(m.test_id, m.check_id) for m in db.exec(select(TestCheck)).all()} == {(script.id, script_check.id)}
    assert _ids(db, Suite) == {suite.id}
    assert [m.test_id for m in db.exec(select(SuiteMember)).all()] == [script.id]
    assert _ids(db, CheckRun, "run_id") == script_runs
    assert {o.run_id for o in db.exec(select(StageOutcome)).all()} == script_runs
    assert _ids(db, RunRequest) == {install["mixed"].id, install["legacy_running"].id, install["script_only"].id}
    assert _ids(db, EntityVersion) == script_versions
    assert not _has_worked_examples(db)


@pytest.mark.parametrize("how", sorted(PURGES))
def test_it_records_one_audit_event_naming_what_it_removed(db, install, how):
    PURGES[how](db)

    (event_,) = db.exec(select(AuditEvent).where(AuditEvent.action == "catalog.upgrade")).all()
    assert event_.actor == "seed" and event_.actor_kind == "system" and event_.entity_type == "catalog"
    assert event_.after["tests"] == 2 and event_.after["check_runs"] == 5
    assert event_.after["deleted_test_names"] == ["Cluster policy enforcement", "My structured test"]
    assert event_.after["affected_suite_names"] == ["Connectivity Suite"]
    assert "2 Tests" in event_.summary and "3 worked examples" in event_.summary
    assert db.exec(select(AuditEvent).where(AuditEvent.action == "test.create",
                                            AuditEvent.actor == "bob")).one()


@pytest.mark.parametrize("how", sorted(PURGES))
def test_a_script_check_left_behind_by_a_deleted_test_goes_too(db, how):
    rolled_back, _ = _structured(db, "Rolled back to structured", "old")
    _, (kept,) = _script(db, "Still a script")
    orphan = Check(display_name="new in the script", handler_key="script",
                   hints={"script_test_id": rolled_back.id, "script_check_name": "new in the script"})
    db.add(orphan)
    kept.hints = {**kept.hints, "script_test_id": rolled_back.id}
    db.add(kept)
    db.commit()
    orphan_id, kept_id = orphan.id, kept.id

    PURGES[how](db)

    assert db.get(Check, orphan_id) is None
    assert db.get(Check, kept_id) is not None, "a Check a surviving Test lists is kept"


@pytest.mark.parametrize("how", sorted(PURGES))
def test_after_either_one_the_startup_purge_finds_nothing_and_writes_nothing(db, install, how):
    PURGES[how](db)
    statements: list[str] = []
    engine = db.get_bind()
    listener = lambda conn, cursor, statement, *_: statements.append(statement)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        counts = purge_legacy(db, actor="seed")
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert set(counts.values()) == {0}
    assert statements and all(s.lstrip().upper().startswith(("SELECT", "PRAGMA")) for s in statements), statements
    assert len(db.exec(select(AuditEvent).where(AuditEvent.action == "catalog.upgrade")).all()) == 1


def test_the_migration_after_the_startup_purge_finds_nothing(db, install):
    purge_legacy(db, actor="seed")
    counts = {m: len(db.exec(select(m)).all()) for m in (Test, Check, CheckRun, RunRequest, AuditEvent)}

    _migrate(db)

    assert {m: len(db.exec(select(m)).all()) for m in counts} == counts


def test_the_migration_follows_the_current_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(MIGRATION.parent.parent))
    assert ScriptDirectory.from_config(config).get_heads() == ["wf43legacypurge"]


def test_a_freshly_seeded_database_has_nothing_to_purge(db):
    from app.services.seed import seed_all

    seed_all(db, with_demo_targets=False)
    counts = {m: len(db.exec(select(m)).all()) for m in (Test, Check, TestCheck, Suite, SuiteMember, EntityVersion)}

    assert set(purge_legacy(db, actor="seed").values()) == {0}
    assert {m: len(db.exec(select(m)).all()) for m in counts} == counts


@pytest.mark.parametrize("failing", ["_drop_worked_examples", "record_audit"])
def test_a_failure_part_way_deletes_nothing(db, install, monkeypatch, failing):
    before = {m: len(db.exec(select(m)).all())
              for m in (Test, Check, TestCheck, SuiteMember, CheckRun, StageOutcome, RunRequest, EntityVersion)}

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    if failing == "_drop_worked_examples":
        original = legacy_purge._drop_worked_examples
        monkeypatch.setattr(legacy_purge, failing, lambda s: original(s) and boom())
    else:
        monkeypatch.setattr(legacy_purge, failing, boom)
    with pytest.raises(RuntimeError, match="boom"):
        purge_legacy(db, actor="seed")

    assert {m: len(db.exec(select(m)).all()) for m in before} == before
    assert _has_worked_examples(db)
    assert db.connection().exec_driver_sql("SELECT count(*) FROM worked_examples").scalar() == 3
