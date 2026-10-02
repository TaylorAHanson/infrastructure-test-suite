"""seed_all() on a database an older release seeded: every structured Test (the
tutorial ones, retired ones, a person's own) goes with its Checks, results and
the worked examples; the Connectivity Suite keeps its id and holds the three
script rewrites; script Tests and their history are untouched; and a second
start changes nothing."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlmodel import select

from app.core.enums import RunStatus, TestStatus
from app.models import AuditEvent, Check, CheckRun, RunRequest, StageOutcome, Suite, SuiteMember, Test, TestCheck
from app.services.script_tests import create_script_test
from app.services.seed import seed_all

CONNECTIVITY = ["Egress connectivity", "SQL Warehouse validation", "Unity Catalog access"]
TUTORIAL = ["Cluster policy enforcement", "Platform self-check"]


def _test(s, name: str) -> Test:
    return s.exec(select(Test).where(Test.name == name)).one()


def _suite(s, name: str) -> Suite:
    return s.exec(select(Suite).where(Suite.name == name)).one()


def _members(s, suite: Suite) -> list[Test]:
    rows = s.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)
                  .order_by(SuiteMember.sort_order)).all()
    return [s.get(Test, m.test_id) for m in rows]


def _structured(s, name: str, handler: str, *, tags: list[str]) -> tuple[Test, Check]:
    test = Test(name=name, tags=tags, enabled="retired" not in tags)
    check = Check(display_name=f"{name} check", handler_key=handler,
                  operation={"service": "statement_execution", "method": "execute_statement"})
    s.add_all([test, check])
    s.commit()
    s.add(TestCheck(test_id=test.id, check_id=check.id, sort_order=0))
    s.commit()
    return test, check


def _drop_script_test(s, test: Test) -> None:
    check_ids = [link.check_id for link in s.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all()]
    for row in [*s.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all(),
                *s.exec(select(SuiteMember).where(SuiteMember.test_id == test.id)).all()]:
        s.delete(row)
    s.flush()
    for check_id in check_ids:
        s.delete(s.get(Check, check_id))
    s.delete(test)
    s.commit()


def _record_run(s, status: RunStatus, *results: tuple[Test, Check]) -> RunRequest:
    request = RunRequest(status=status)
    s.add(request)
    s.commit()
    for test, check in results:
        run = CheckRun(request_id=request.id, test_id=test.id, check_id=check.id, verdict=RunStatus.PASS)
        s.add(run)
        s.commit()
        s.add(StageOutcome(run_id=run.run_id, stage="OPERATE"))
    s.commit()
    return request


@pytest.fixture()
def old_install(session):
    """Today's script catalog as the previous release left it: the connectivity Tests
    still structured, the tutorial Tests, a retired Test in a retired seeded Suite,
    a person's structured and script Tests, run history and the worked examples."""
    seed_all(session, with_demo_targets=False)
    connectivity_suite = _suite(session, "Connectivity Suite")
    full = _suite(session, "Full Regression")
    for name in CONNECTIVITY:
        _drop_script_test(session, _test(session, name))

    old: dict[str, tuple[Test, Check]] = {}
    for name, handler in zip(CONNECTIVITY + TUTORIAL,
                             ["outbound_http", "sql_warehouse", "sql_warehouse", "databricks_sdk", "lakebase"]):
        old[name] = _structured(session, name, handler, tags=["tutorial", "example"])
    old["SSO enforcement (retired)"] = _structured(session, "SSO enforcement (retired)", "databricks_sdk",
                                                   tags=["security", "retired"])
    old["My structured test"] = _structured(session, "My structured test", "databricks_sdk", tags=[])
    session.add(AuditEvent(action="test.create", entity_type="test", actor="bob",
                           entity_id=old["My structured test"][0].id))
    retired_suite = Suite(name="Security Posture Suite", tags=["seeded"])
    session.add(retired_suite)
    session.commit()
    session.add(SuiteMember(suite_id=retired_suite.id, test_id=old["SSO enforcement (retired)"][0].id))
    for i, name in enumerate(CONNECTIVITY):
        session.add(SuiteMember(suite_id=connectivity_suite.id, test_id=old[name][0].id, sort_order=i))
    for i, name in enumerate(CONNECTIVITY + TUTORIAL, start=100):
        session.add(SuiteMember(suite_id=full.id, test_id=old[name][0].id, sort_order=i))
    session.commit()

    mine, _ = create_script_test(session, name="My script test", actor="bob", status=TestStatus.ACTIVE,
                                 script='with check("mine"):\n    assert w.current_user.me()\n')
    workspace_access = _test(session, "Workspace access")
    wa_check = session.get(Check, session.exec(select(TestCheck.check_id)
                                               .where(TestCheck.test_id == workspace_access.id)).first())
    legacy_only = _record_run(session, RunStatus.FAIL, old["SQL Warehouse validation"], old["Platform self-check"])
    mixed = _record_run(session, RunStatus.PASS, old["Egress connectivity"], (workspace_access, wa_check))

    connection = session.connection()
    connection.exec_driver_sql("CREATE TABLE worked_examples (id VARCHAR PRIMARY KEY, intent VARCHAR)")
    connection.exec_driver_sql("INSERT INTO worked_examples VALUES ('ex1', 'list clusters'), ('ex2', 'who am i')")
    session.commit()
    return {"connectivity_suite": connectivity_suite.id, "mine": mine.id, "workspace_access": workspace_access.id,
            "legacy_only": legacy_only.id, "mixed": mixed.id}


def test_every_structured_test_and_its_history_is_gone(session, old_install):
    seed_all(session, with_demo_targets=False)

    assert all(t.script for t in session.exec(select(Test)).all())
    assert {c.handler_key for c in session.exec(select(Check)).all()} == {"script"}
    assert not {"Platform self-check", "Cluster policy enforcement", "My structured test",
                "SSO enforcement (retired)"} & {t.name for t in session.exec(select(Test)).all()}
    assert session.get(RunRequest, old_install["legacy_only"]) is None
    (kept,) = session.exec(select(CheckRun).where(CheckRun.request_id == old_install["mixed"])).all()
    assert kept.test_id == old_install["workspace_access"]
    assert {o.run_id for o in session.exec(select(StageOutcome)).all()} == {kept.run_id}
    assert not inspect(session.connection()).has_table("worked_examples")


def test_the_connectivity_suite_keeps_its_id_and_holds_the_script_rewrites(session, old_install):
    seed_all(session, with_demo_targets=False)

    suite = session.get(Suite, old_install["connectivity_suite"])
    assert suite.name == "Connectivity Suite" and suite.version == 2
    members = _members(session, suite)
    assert [t.name for t in members] == CONNECTIVITY
    assert all(t.script for t in members)
    full = {t.name for t in _members(session, _suite(session, "Full Regression"))}
    assert full == {t.name for t in session.exec(select(Test)).all()} - {"My script test"}


def test_script_tests_and_retired_suites_are_handled_as_before(session, old_install):
    seed_all(session, with_demo_targets=False)

    assert session.get(Test, old_install["mine"]).enabled
    assert not session.exec(select(Suite).where(Suite.name == "Security Posture Suite")).all()


def test_the_upgrade_is_audited_and_a_second_start_changes_nothing(session, old_install):
    seed_all(session, with_demo_targets=False)
    purge, upgrade = session.exec(select(AuditEvent).where(AuditEvent.action == "catalog.upgrade")
                                  .order_by(AuditEvent.ts)).all()
    assert purge.after["deleted_test_names"] == sorted(CONNECTIVITY + TUTORIAL + [
        "My structured test", "SSO enforcement (retired)"])
    assert purge.after["worked_examples"] == 2
    assert upgrade.after["added_tests"] == 3 and upgrade.after["retired_suites"] == 1

    models = (Test, Check, TestCheck, Suite, SuiteMember, CheckRun, RunRequest, AuditEvent)
    counts = [len(session.exec(select(m)).all()) for m in models]
    seed_all(session, with_demo_targets=False)
    assert [len(session.exec(select(m)).all()) for m in models] == counts
