"""/admin/purge-* delete in bulk (one statement per table, children first) and
leave an audit record naming who purged what — purge-all removes every Target,
Test and Suite, so it must never be silent."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import select

from app.db.session import get_engine, session_scope
from app.models import AuditEvent, Check, CheckRun, RunRequest, StageOutcome, Suite, Target
from app.models import Test as CatalogTest


@contextmanager
def _statements():
    seen: list[str] = []

    def _capture(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        seen.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _capture)


def _seed_runs(n: int) -> None:
    with session_scope() as s:
        check_id = s.exec(select(Check.id)).first()
        request = RunRequest()
        s.add(request)
        s.commit()
        for _ in range(n):
            run = CheckRun(check_id=check_id, request_id=request.id, gating=True)
            s.add(run)
            s.flush()
            s.add(StageOutcome(run_id=run.run_id, stage="OPERATE"))
        s.commit()


def _count(model) -> int:
    with session_scope() as s:
        return len(s.exec(select(model)).all())


def test_purge_runs_deletes_in_bulk_and_is_audited(client):
    _seed_runs(12)
    checks_before = _count(Check)

    with _statements() as seen:
        resp = client.post("/api/v1/admin/purge-runs", params={"actor": "alice@example.com"})
    assert resp.status_code == 200, resp.text
    counts = resp.json()["counts"]
    assert counts == {"StageOutcome": 12, "CheckRun": 12, "RunRequest": 1}

    deletes = [s for s in seen if s.lstrip().upper().startswith("DELETE")]
    assert [d.split()[2] for d in deletes] == ["stage_outcomes", "check_runs", "run_requests"]
    assert not any(s.lstrip().upper().startswith("SELECT") and "check_runs" in s for s in seen)
    assert _count(CheckRun) == _count(StageOutcome) == _count(RunRequest) == 0
    assert _count(Check) == checks_before

    with session_scope() as s:
        audit = s.exec(select(AuditEvent).where(AuditEvent.action == "admin.purge_runs")).one()
    assert audit.actor == "alice@example.com"
    assert audit.after == counts
    assert "12 CheckRun" in audit.summary


def test_purge_all_is_audited_and_visible_in_the_audit_view(client):
    _seed_runs(3)
    assert _count(CatalogTest) and _count(Suite)

    resp = client.post("/api/v1/admin/purge-all", params={"actor": "bob@example.com"})
    assert resp.status_code == 200, resp.text
    counts = resp.json()["counts"]
    assert counts["CheckRun"] == 3 and counts["Test"] > 0 and counts["Suite"] > 0
    assert _count(CatalogTest) == _count(Suite) == _count(Check) == _count(Target) == 0

    listed = client.get("/api/v1/audit", params={"event_type": "admin.purge_all"}).json()
    assert len(listed) == 1
    assert listed[0]["actor"] == "bob@example.com"
    assert listed[0]["after"] == counts
    assert "admin.purge_all" in client.get("/api/v1/audit/event-types").json()


def test_catalog_upgrade_records_are_visible_in_the_audit_view(client):
    from app.services.audit import CATALOG_UPGRADE, record_audit

    with session_scope() as s:
        record_audit(s, actor="seed", actor_kind="system", action=CATALOG_UPGRADE,
                     entity_type="catalog", summary="upgrade")
    listed = client.get("/api/v1/audit", params={"event_type": CATALOG_UPGRADE}).json()
    assert [e["summary"] for e in listed] == ["upgrade"]
