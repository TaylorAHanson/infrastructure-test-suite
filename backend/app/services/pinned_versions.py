"""A Test's version history pins its member Checks' content (#118, ADR-0001/0006 §8).

**Pin, don't copy.** A Test snapshot records its script and each member's
``check_version`` beside its ``check_id``; the Check content itself stays
versioned on the Check. So a Test version is a faithful record of what the Test
did, while Check ids — which run history, last-good, findings and flaky
detection key on — never change.

- Saving a script (:func:`app.services.script_tests.save_script`) or editing the
  Test's fields (:func:`edit_test`) is a new Test version.
- Rolling a Test back (:func:`rollback_test`) restores its script, membership
  and each member Check's pinned content (as a new Check version) — never its
  status or enabled flag. A version with no script can't be restored.
- :func:`test_read` exposes ``shared_with`` per member: the other Tests listing
  the same Check.

Every pinned Check version is restorable: before a Test version is recorded, a
member Check whose current version was never snapshotted is backfilled.
Likewise a Test's own current version is backfilled before it is first edited,
so a seeded Test's v1 can be restored.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.core.ids import utcnow
from app.models import Check, Test, TestCheck
from app.schemas.schemas import TestCheckIn, TestUpdate
from app.services import versioning
from app.services.audit import record_audit, version_pointer

#: Check columns a restore never overwrites (identity + version bookkeeping).
_CHECK_FIXED = ("id", "version", "created_at", "updated_at")
#: Test columns a rollback restores from the snapshot — content only. Lifecycle
#: (``status``: an Active Test rolled back to its Draft v1 must stay Active) and
#: the ``enabled`` switch stay as they are now, or a rollback would silently drop
#: the Test from every run.
_TEST_RESTORED = ("name", "description", "tags", "run_as_role", "origin",
                  "derivation", "script")
_TEST_EDITABLE = ("name", "description", "tags", "run_as_role", "enabled")


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def test_snapshot(session: Session, test: Test) -> dict:
    """The Test's content: its fields + ordered members, each pinning its Check version."""
    rows = _member_rows(session, test.id)
    versions = {c.id: c.version for c in session.exec(
        select(Check).where(Check.id.in_([m.check_id for m in rows]))  # type: ignore[union-attr]
    ).all()}
    return {
        **test.model_dump(mode="json"),
        "members": [{"check_id": m.check_id, "check_version": versions.get(m.check_id),
                     "kind": m.kind, "run_as_role": m.run_as_role,
                     "counterpart_check_id": m.counterpart_check_id, "sort_order": m.sort_order}
                    for m in rows],
    }


def test_read(session: Session, test: Test) -> dict:
    """:func:`test_snapshot` plus, per member, the *other* Tests sharing that Check.

    ``shared_with`` is read-time context (so the UI can warn that an edit or
    rollback reaches other Tests), not Test content — it is never snapshotted.
    """
    out = test_snapshot(session, test)
    owners: dict[str, set[str]] = {}
    for row in session.exec(select(TestCheck).where(
            TestCheck.check_id.in_([m["check_id"] for m in out["members"]]))).all():  # type: ignore[attr-defined]
        owners.setdefault(row.check_id, set()).add(row.test_id)
    for m in out["members"]:
        m["shared_with"] = sorted(owners.get(m["check_id"], set()) - {test.id})
    return out


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def set_members(session: Session, test_id: str, members: list[TestCheckIn]) -> None:
    for m in _member_rows(session, test_id):
        session.delete(m)
    # A flush orders INSERTs before DELETEs, so re-adding a kept Check would
    # trip the (test_id, check_id) unique index — delete first.
    session.flush()
    for i, m in enumerate(members):
        session.add(TestCheck(
            test_id=test_id, check_id=m.check_id, sort_order=i, kind=m.kind,
            run_as_role=m.run_as_role, counterpart_check_id=m.counterpart_check_id,
        ))


def record_test_version(session: Session, test: Test, actor: str) -> dict:
    """Snapshot the Test as ``test.version``, first making every pin restorable."""
    for row in _member_rows(session, test.id):
        check = session.get(Check, row.check_id)
        if check is not None:
            _ensure_check_recorded(session, check)
    out = test_snapshot(session, test)
    versioning.snapshot(session, "test", test.id, test.version, out, actor)
    return out


def edit_test(session: Session, test: Test, payload: TestUpdate, actor: str) -> Test:
    """Apply an edit to the Test's fields as one new version."""
    _ensure_test_recorded(session, test)
    before_version = test.version
    data = payload.model_dump(exclude_unset=True)
    for key in _TEST_EDITABLE:
        if key in data and data[key] is not None:
            setattr(test, key, data[key])
    test.version += 1
    test.updated_at = utcnow()
    session.add(test)
    session.commit()
    session.refresh(test)
    record_test_version(session, test, actor)
    record_audit(session, actor=actor, action="test.update", entity_type="test", entity_id=test.id,
                 summary=f"Updated test {test.name} (v{before_version} → v{test.version})",
                 before=version_pointer("test", test.id, before_version),
                 after=version_pointer("test", test.id, test.version))
    return test


def rollback_test(session: Session, test: Test, version: int, actor: str) -> Test:
    """Restore a prior Test version — script, fields, membership and pinned Check content.

    Lands as a new current version (ADR-0001). Each pinned member Check whose
    content differs from its pin is restored as a new Check version. Raises
    :class:`LookupError` if the version (or a pinned Check version) is missing,
    and :class:`ValueError` if the version has no script, before writing.
    """
    snap = versioning.get_version(session, "test", test.id, version)
    if snap is None:
        raise LookupError("version not found")
    data = snap.snapshot
    if not data.get("script"):
        raise ValueError(f"v{version} has no script, so it can't be restored")
    pins: list[tuple[Check, dict]] = []
    for m in data.get("members") or []:
        check = session.get(Check, m["check_id"])
        if check is None or m.get("check_version") is None:
            continue
        pinned = versioning.get_version(session, "check", check.id, m["check_version"])
        if pinned is None:
            raise LookupError(f"check {check.id} v{m['check_version']} not found")
        pins.append((check, pinned.snapshot))

    _ensure_test_recorded(session, test)
    before_version = test.version
    for key in _TEST_RESTORED:
        if key in data:
            setattr(test, key, data[key])
    if isinstance(data.get("members"), list):
        set_members(session, test.id, [
            TestCheckIn(check_id=m["check_id"], kind=m.get("kind", "assertion"),
                        run_as_role=m.get("run_as_role"),
                        counterpart_check_id=m.get("counterpart_check_id"))
            for m in data["members"]
        ])
    for check, pinned in pins:
        fields = _restorable(check, pinned)
        current = check.model_dump(mode="json")
        if any(current.get(k) != v for k, v in fields.items()):
            _write_check_version(
                session, check, fields, actor, action="check.rollback",
                summary=(f"Restored check {check.display_name} to v{pinned.get('version')} "
                         f"(test {test.name} rolled back to v{version})"))
    test.version += 1
    test.updated_at = utcnow()
    session.add(test)
    session.commit()
    session.refresh(test)
    record_test_version(session, test, actor)
    record_audit(session, actor=actor, action="test.rollback", entity_type="test", entity_id=test.id,
                 summary=f"Rolled test back to v{version} (new v{test.version})",
                 before=version_pointer("test", test.id, before_version),
                 after=version_pointer("test", test.id, test.version))
    return test


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _member_rows(session: Session, test_id: str) -> list[TestCheck]:
    return list(session.exec(
        select(TestCheck).where(TestCheck.test_id == test_id).order_by(TestCheck.sort_order)  # type: ignore[arg-type]
    ).all())


def _restorable(check: Check, snapshot: dict) -> dict:
    return {k: v for k, v in snapshot.items() if hasattr(check, k) and k not in _CHECK_FIXED}


def _write_check_version(session: Session, check: Check, fields: dict, actor: str, *,
                         action: str, summary: str) -> dict:
    """Set ``fields`` on the Check as its next version, snapshotted and audited."""
    _ensure_check_recorded(session, check)
    before = check.model_dump(mode="json")
    for key, value in fields.items():
        setattr(check, key, value)
    check.version += 1
    check.updated_at = utcnow()
    session.add(check)
    session.commit()
    session.refresh(check)
    out = check.model_dump(mode="json")  # capture before follow-up commits expire the row
    versioning.snapshot(session, "check", check.id, check.version, out, actor)
    record_audit(session, actor=actor, action=action, entity_type="check", entity_id=check.id,
                 summary=summary, before=before, after=out)
    return out


def _ensure_check_recorded(session: Session, check: Check) -> None:
    """Backfill a snapshot of the Check's current version if it was never recorded."""
    if versioning.get_version(session, "check", check.id, check.version) is None:
        versioning.snapshot(session, "check", check.id, check.version, check.model_dump(mode="json"))


def _ensure_test_recorded(session: Session, test: Test) -> None:
    """Backfill a snapshot of the Test's current version if it was never recorded."""
    if versioning.get_version(session, "test", test.id, test.version) is None:
        record_test_version(session, test, actor="system")
