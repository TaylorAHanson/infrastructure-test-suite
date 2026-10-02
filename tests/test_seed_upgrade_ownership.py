"""The catalog upgrade runs on every startup, so it may only touch what the seeder
made and nobody has since edited — and it audits every change it makes.

* S15 — the CI Gate is reset only when it is still the seeder's and differs
  from the bundled gate; a person's edit to it survives restarts.
* S16 — retirement matches seeded rows only: a person's Test or Suite holding a
  retired name is left alone, and only unedited seeded Suites are removed.
* S17 — seeded Tests are wired to the seeder's own Checks, never to a person's
  Check that shares a display name.
* S18 — seeded script Tests nobody edited follow catalog fixes as a new
  version; edited, renamed or deleted seeded rows are not reverted, duplicated
  or recreated.
"""
from __future__ import annotations

from sqlmodel import select

from app.db.session import session_scope
from app.models import AuditEvent, Check, EntityVersion, Suite, SuiteMember, TestCheck
from app.models import Test as CatalogTest
from app.services.seed import CHECK_SPECS, seed_all
from app.services.seed.build import SEED_KEY_HINT, make_check

GATE = "CI Gate"
POSTURE_TEST = "Admin footprint"


def _restart() -> None:
    with session_scope() as s:
        seed_all(s, with_demo_targets=False)


def _suite(s, name: str) -> Suite:
    return s.exec(select(Suite).where(Suite.name == name)).one()


def _test(s, name: str) -> CatalogTest:
    return s.exec(select(CatalogTest).where(CatalogTest.name == name)).one()


def _member_names(s, suite: Suite) -> list[str]:
    rows = s.exec(select(SuiteMember).where(SuiteMember.suite_id == suite.id)
                  .order_by(SuiteMember.sort_order)).all()
    return [s.get(CatalogTest, m.test_id).name for m in rows]


def _seed_audits(action: str) -> list[AuditEvent]:
    with session_scope() as s:
        return list(s.exec(select(AuditEvent).where(AuditEvent.actor == "seed",
                                                    AuditEvent.action == action)).all())


# ------------------------------------- S15 -------------------------------------
def test_a_restart_with_nothing_changed_writes_nothing(client):
    with session_scope() as s:
        before = _suite(s, GATE).version
    _restart()
    with session_scope() as s:
        assert _suite(s, GATE).version == before
    assert _seed_audits("suite.update") == []
    assert _seed_audits("catalog.upgrade") == []


def test_a_persons_ci_gate_edit_survives_restarts(client):
    with session_scope() as s:
        gate = _suite(s, GATE)
        keep = s.get(CatalogTest, s.exec(select(SuiteMember.test_id)
                                         .where(SuiteMember.suite_id == gate.id)).first())
        gate_id, keep_id, keep_name = gate.id, keep.id, keep.name
    resp = client.patch(f"/api/v1/suites/{gate_id}", json={"test_ids": [keep_id]})
    assert resp.status_code == 200, resp.text

    _restart()
    _restart()
    with session_scope() as s:
        assert _member_names(s, _suite(s, GATE)) == [keep_name]
    assert _seed_audits("suite.update") == []


def test_an_unedited_gate_is_reset_to_the_bundled_gate_and_audited(client):
    with session_scope() as s:
        gate = _suite(s, GATE)
        bundled = _member_names(s, gate)
        # An older release seeded a different gate: still the seeder's (v1, never
        # snapshotted), just stale.
        for m in s.exec(select(SuiteMember).where(SuiteMember.suite_id == gate.id)).all():
            s.delete(m)
        s.add(SuiteMember(suite_id=gate.id, test_id=_test(s, POSTURE_TEST).id, sort_order=0))
        s.commit()

    _restart()
    with session_scope() as s:
        gate = _suite(s, GATE)
        assert _member_names(s, gate) == bundled
        assert gate.version == 2
        snap = s.exec(select(EntityVersion).where(EntityVersion.entity_id == gate.id,
                                                  EntityVersion.version == 2)).one()
        assert snap.actor == "seed"
    (audit,) = _seed_audits("suite.update")
    assert audit.entity_id == gate.id
    assert audit.before["test_ids"] != audit.after["test_ids"]

    _restart()  # now current — and still the seeder's — so nothing more happens
    assert len(_seed_audits("suite.update")) == 1


# ------------------------------------- S16 -------------------------------------
def test_a_persons_test_with_a_retired_name_is_not_retired(client):
    resp = client.post("/api/v1/tests", json={"name": "SSO enforcement", "description": "mine"})
    assert resp.status_code in (200, 201), resp.text
    tid = resp.json()["id"]

    _restart()
    with session_scope() as s:
        test = s.get(CatalogTest, tid)
        assert test.name == "SSO enforcement" and test.enabled and "retired" not in test.tags


def test_only_unedited_seeded_suites_with_retired_names_are_removed(client):
    with session_scope() as s:
        old = Suite(name="Consumer Suite", tags=["seeded"])
        edited = Suite(name="Producer Suite", tags=["seeded"])
        s.add_all([old, edited])
        s.commit()
        old_id, edited_id = old.id, edited.id
    mine = client.post("/api/v1/suites", json={"name": "Processing Suite", "test_ids": []}).json()["id"]
    client.patch(f"/api/v1/suites/{edited_id}", json={"description": "we still use this"})

    _restart()
    with session_scope() as s:
        assert s.get(Suite, old_id) is None
        assert s.get(Suite, edited_id) is not None
        assert s.get(Suite, mine) is not None
    (deleted,) = _seed_audits("suite.delete")
    assert deleted.entity_id == old_id and deleted.before["name"] == "Consumer Suite"


# ------------------------------------- S17 -------------------------------------
_TUTORIAL_KEY, _TUTORIAL_SPEC = CHECK_SPECS[0]


def _drop_tutorial_test_using(s, check_key: str) -> str:
    """Remove the seeded Test that uses ``check_key``'s Check, as an older release lacked it."""
    check = next(c for c in s.exec(select(Check)).all()
                 if (c.hints or {}).get(SEED_KEY_HINT) == check_key)
    link = s.exec(select(TestCheck).where(TestCheck.check_id == check.id)).first()
    test = s.get(CatalogTest, link.test_id)
    name = test.name
    for row in s.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all():
        s.delete(row)
    for row in s.exec(select(SuiteMember).where(SuiteMember.test_id == test.id)).all():
        s.delete(row)
    s.delete(test)
    s.commit()
    return name


def test_seeded_tests_are_not_wired_to_a_persons_same_named_check(client):
    with session_scope() as s:
        name = _drop_tutorial_test_using(s, _TUTORIAL_KEY)
        for c in s.exec(select(Check)).all():
            if (c.hints or {}).get(SEED_KEY_HINT) == _TUTORIAL_KEY:
                c.enabled = False  # the seeder's own copy is gone too
                s.add(c)
        s.commit()
    mine = client.post("/api/v1/checks", json={
        "display_name": _TUTORIAL_SPEC["display_name"],
        "operation": {"service": "jobs", "method": "list"},
        "assertion": "the operation succeeds"}).json()["id"]

    _restart()
    with session_scope() as s:
        test = _test(s, name)
        member_ids = {m.check_id for m in s.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all()}
        assert mine not in member_ids
        wired = [s.get(Check, cid) for cid in member_ids]
        assert any((c.hints or {}).get(SEED_KEY_HINT) == _TUTORIAL_KEY for c in wired)


def test_a_check_seeded_before_keys_existed_is_still_reused(client):
    with session_scope() as s:
        name = _drop_tutorial_test_using(s, _TUTORIAL_KEY)
        for c in s.exec(select(Check)).all():
            if (c.hints or {}).get(SEED_KEY_HINT) == _TUTORIAL_KEY:
                c.enabled = False
                s.add(c)
        legacy = make_check(_TUTORIAL_SPEC)  # no seed key, as an older release built it
        holder = CatalogTest(name="An older seeded test")
        s.add_all([legacy, holder])
        s.commit()
        s.add(TestCheck(test_id=holder.id, check_id=legacy.id, sort_order=0))
        s.commit()
        legacy_id = legacy.id

    _restart()
    with session_scope() as s:
        test = _test(s, name)
        member_ids = {m.check_id for m in s.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all()}
        assert legacy_id in member_ids


# ------------------------------------- S18 -------------------------------------
def _age_script(s, name: str) -> tuple[str, str, set[str]]:
    """Make a seeded script Test look like an older release seeded it (v1, old script)."""
    test = _test(s, name)
    bundled = test.script
    test.script = bundled.replace(" ", "  ", 1)
    s.add(test)
    s.commit()
    checks = {m.check_id for m in s.exec(select(TestCheck).where(TestCheck.test_id == test.id)).all()}
    return test.id, bundled, checks


def test_an_unedited_seeded_script_test_follows_catalog_fixes(client):
    with session_scope() as s:
        tid, bundled, checks = _age_script(s, POSTURE_TEST)

    _restart()
    with session_scope() as s:
        test = s.get(CatalogTest, tid)
        assert test.script == bundled
        assert test.version == 2
        assert {m.check_id for m in s.exec(select(TestCheck).where(TestCheck.test_id == tid)).all()} == checks
    (update,) = [a for a in _seed_audits("test.update") if a.entity_id == tid]
    assert update.after == {"entity_type": "test", "entity_id": tid, "version": 2}

    _restart()
    with session_scope() as s:
        assert s.get(CatalogTest, tid).version == 2


def test_a_seeded_script_test_a_person_edited_is_not_overwritten(client):
    with session_scope() as s:
        tid, bundled, _ = _age_script(s, POSTURE_TEST)
        aged = s.get(CatalogTest, tid).script
    client.patch(f"/api/v1/tests/{tid}", json={"description": "tuned for our workspace"})

    _restart()
    with session_scope() as s:
        test = s.get(CatalogTest, tid)
        assert test.script == aged != bundled
        assert test.description == "tuned for our workspace"
    assert [a for a in _seed_audits("test.update") if a.entity_id == tid] == []


def test_a_renamed_seeded_test_is_not_duplicated(client):
    with session_scope() as s:
        tid = _test(s, POSTURE_TEST).id
        count = len(s.exec(select(CatalogTest)).all())
    client.patch(f"/api/v1/tests/{tid}", json={"name": "Our admin footprint"})

    _restart()
    with session_scope() as s:
        assert len(s.exec(select(CatalogTest)).all()) == count
        assert not s.exec(select(CatalogTest).where(CatalogTest.name == POSTURE_TEST)).all()


def test_a_seeded_suite_a_person_deleted_is_not_recreated(client):
    with session_scope() as s:
        sid = _suite(s, "Security posture (drift)").id
    assert client.delete(f"/api/v1/suites/{sid}").status_code == 204

    _restart()
    with session_scope() as s:
        assert not s.exec(select(Suite).where(Suite.name == "Security posture (drift)")).all()
