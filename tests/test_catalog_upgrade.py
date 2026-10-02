"""seed_all() on a database an older release seeded: retire what it seeded, add
what the current catalog has, leave a person's own Tests alone, and keep every
retired row so run history still resolves."""
from __future__ import annotations

from sqlmodel import select

from app.models import Check, Suite, SuiteMember, Test, TestCheck
from app.services.seed import seed_all
from app.services.seed.build import make_check
from app.services.seed.upgrade import RETIRED_TAG


def _structured(session, name: str, check_name: str) -> Test:
    check = make_check({"display_name": check_name, "operation": {"service": "clusters", "method": "list"},
                        "assertion": "the operation succeeds"})
    test = Test(name=name, tags=["posture"])
    session.add_all([check, test])
    session.commit()
    session.add(TestCheck(test_id=test.id, check_id=check.id, sort_order=0, kind="assertion"))
    session.commit()
    return test


def _suite(session, name: str, tests: list[Test]) -> Suite:
    suite = Suite(name=name, tags=["seeded"])
    session.add(suite)
    session.commit()
    for i, t in enumerate(tests):
        session.add(SuiteMember(suite_id=suite.id, test_id=t.id, sort_order=i))
    session.commit()
    return suite


def _old_catalog(session):
    sso = _structured(session, "SSO enforcement", "SSO enabled")
    exfil = _structured(session, "Exfiltration controls", "Exfiltration controls disabled")
    serverless = _structured(session, "Serverless compute", "Serverless SQL is enabled")
    mine = _structured(session, "My own test", "my check")
    _suite(session, "Security Posture Suite", [sso, exfil])
    _suite(session, "CI Gate", [sso, exfil])
    _suite(session, "Full Regression", [sso, exfil, serverless])
    return {"sso": sso, "exfil": exfil, "serverless": serverless, "mine": mine}


def _members(session, suite_name: str) -> set[str]:
    suite = session.exec(select(Suite).where(Suite.name == suite_name)).one()
    return {session.get(Test, m.test_id).name
            for m in session.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)).all()}


def test_superseded_seeded_tests_are_retired_in_place(session):
    old = _old_catalog(session)
    seed_all(session, with_demo_targets=False)

    for key in ("sso", "exfil", "serverless"):
        test = session.get(Test, old[key].id)
        assert test is not None, "a retired Test is kept for its run history"
        assert test.enabled is False and RETIRED_TAG in test.tags and test.name.endswith(" (retired)")
        assert not session.exec(select(SuiteMember).where(SuiteMember.test_id == test.id)).all()
        (link,) = session.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all()
        assert session.get(Check, link.check_id).enabled is False


def test_a_persons_own_test_is_untouched(session):
    old = _old_catalog(session)
    seed_all(session, with_demo_targets=False)
    mine = session.get(Test, old["mine"].id)
    assert mine.name == "My own test" and mine.enabled and RETIRED_TAG not in mine.tags


def test_the_current_catalog_is_added_and_its_names_are_taken_by_scripts(session):
    _old_catalog(session)
    seed_all(session, with_demo_targets=False)
    live = {t.name: t for t in session.exec(select(Test)).all() if RETIRED_TAG not in (t.tags or [])}
    for name in ("Workspace access", "Unity Catalog data path", "Exfiltration controls",
                 "Serverless compute", "SQL Warehouse validation"):
        assert name in live, name
    assert live["Exfiltration controls"].script and live["Serverless compute"].script


def test_old_suites_go_and_the_gate_is_rebuilt_exactly(session):
    _old_catalog(session)
    seed_all(session, with_demo_targets=False)
    names = {s.name for s in session.exec(select(Suite)).all()}
    assert "Security Posture Suite" not in names
    assert {"Post-apply health: compute & network", "Security posture (drift)"} <= names
    assert _members(session, "CI Gate") == {"Workspace access", "Unity Catalog data path"}
    full = _members(session, "Full Regression")
    assert "Workspace access" in full and not any(n.endswith(" (retired)") for n in full)


def test_upgrading_twice_changes_nothing(session):
    _old_catalog(session)
    seed_all(session, with_demo_targets=False)
    counts = [len(session.exec(select(m)).all()) for m in (Test, Check, Suite, SuiteMember)]
    seed_all(session, with_demo_targets=False)
    assert [len(session.exec(select(m)).all()) for m in (Test, Check, Suite, SuiteMember)] == counts


def test_retired_tests_are_hidden_from_the_list_but_still_resolve(client):
    from app.db.session import session_scope

    with session_scope() as s:
        old = _structured(s, "SSO enforcement", "SSO enabled (old)")
        old_id = old.id
        seed_all(s, with_demo_targets=False)
    names = {t["name"] for t in client.get("/api/v1/tests").json()}
    assert "SSO enforcement (retired)" not in names
    every = {t["name"] for t in client.get("/api/v1/tests", params={"include_retired": True}).json()}
    assert "SSO enforcement (retired)" in every
    assert client.get(f"/api/v1/tests/{old_id}").status_code == 200
