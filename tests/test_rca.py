"""Service seam: the Assistant's Triage/RCA read-path (ADR-0012 §4, ADR-0009).

Asserts the read-only + suggest-never-assert + Overnight-default invariants the
harness enforces, using a stub model.
"""
from __future__ import annotations

import json

from sqlmodel import select

from app.core.enums import RunStatus
from app.core.ids import utcnow
from app.models import Check, CheckRun, Target
from app.services import rca


def _finding(session, *, first_bad_sha="unknown"):
    """Seed a (check, target) with a green run then a failing run."""
    check = Check(display_name="probe", assertion="the operation succeeds")
    target = Target(name="t1", workspace_name="ws1")
    session.add(check)
    session.add(target)
    session.commit()
    session.refresh(check)
    session.refresh(target)
    green = CheckRun(check_id=check.id, target_id=target.id, verdict=RunStatus.PASS,
                     git_sha="green-sha", created_at=utcnow())
    session.add(green)
    session.commit()
    bad = CheckRun(check_id=check.id, target_id=target.id, verdict=RunStatus.FAIL,
                   failure_bucket="OPERATE_ERROR", failure_stage="OPERATE",
                   git_sha=first_bad_sha, created_at=utcnow())
    session.add(bad)
    session.commit()
    return check, target


def _model(payload: dict):
    def call(messages):
        return json.dumps(payload)
    return call


def test_rca_is_advisory_and_defaults_to_overnight_when_unsure(session):
    check, target = _finding(session, first_bad_sha="unknown")
    # Model over-confidently claims the recent lane, but the first-bad run has no
    # commit sha → the harness overrides to Overnight (ADR-0009 §3).
    s = rca.suggest(session, check_id=check.id, target_id=target.id,
                    model_call=_model({"cause": "a deploy broke it",
                                       "lane": "recent-deployments", "confidence": "high"}))
    assert s.advisory is True
    assert s.suggested_lane == rca.LANE_OVERNIGHT


def test_rca_uses_recent_lane_only_with_a_commit_and_confidence(session):
    check, target = _finding(session, first_bad_sha="abc123")
    s = rca.suggest(session, check_id=check.id, target_id=target.id,
                    model_call=_model({"cause": "commit abc123 regressed OPERATE",
                                       "lane": "recent-deployments", "confidence": "high"}))
    assert s.suggested_lane == rca.LANE_RECENT
    assert s.confidence == "high"


def test_rca_is_read_only(session):
    check, target = _finding(session)
    before = len(session.exec(select(CheckRun)).all())
    rca.suggest(session, check_id=check.id, target_id=target.id,
                model_call=_model({"cause": "x", "lane": "overnight", "confidence": "low"}))
    after = len(session.exec(select(CheckRun)).all())
    assert before == after  # RCA never mutates state (user story 17)


def test_rca_heuristic_without_model_defaults_overnight(session):
    check, target = _finding(session, first_bad_sha="unknown")
    s = rca.suggest(session, check_id=check.id, target_id=target.id, model_call=None)
    assert s.model_available is False
    assert s.suggested_lane == rca.LANE_OVERNIGHT
    assert s.advisory is True
