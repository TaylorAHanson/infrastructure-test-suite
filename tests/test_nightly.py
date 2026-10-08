"""The nightly run: the poller submits one full run per cron slot."""
from __future__ import annotations

import logging
from datetime import datetime

import pytest
from sqlmodel import select

from app.core.config import get_settings
from app.core.enums import Depth, RequestSource, RunStatus
from app.models import AuditEvent, RunRequest
from app.services import nightly


@pytest.fixture()
def schedule(seeded_session, monkeypatch):
    for name in ("_armed_at", "_handled_slot", "_rejected_cron"):
        monkeypatch.setattr(nightly, name, None)
    settings = get_settings()
    monkeypatch.setattr(settings, "NIGHTLY_CRON", "0 7 * * *")
    monkeypatch.setattr(settings, "NIGHTLY_SCHEDULE_STATUS", "UNPAUSED")
    return settings


def _nightly_requests(session) -> list[RunRequest]:
    session.expire_all()
    return session.exec(select(RunRequest).where(RunRequest.request_source == RequestSource.NIGHTLY)).all()


def test_a_slot_that_comes_round_submits_one_full_run_of_everything(schedule, seeded_session):
    assert nightly.fire_if_due(datetime(2026, 10, 8, 6, 59)) is None

    request_id = nightly.fire_if_due(datetime(2026, 10, 8, 7, 0, 5))

    [request] = _nightly_requests(seeded_session)
    assert request.id == request_id
    assert (request.depth, request.status, request.requested_by) == (Depth.FULL, RunStatus.PENDING, "nightly")
    assert (request.suite_ids, request.test_ids, request.target_ids) == ([], [], [])


def test_the_same_slot_never_fires_twice(schedule, seeded_session):
    nightly.fire_if_due(datetime(2026, 10, 8, 6, 59))
    nightly.fire_if_due(datetime(2026, 10, 8, 7, 0, 5))

    assert nightly.fire_if_due(datetime(2026, 10, 8, 7, 0, 10)) is None
    assert nightly.fire_if_due(datetime(2026, 10, 8, 23, 0)) is None
    assert len(_nightly_requests(seeded_session)) == 1


def test_a_slot_missed_while_the_app_was_down_is_skipped_not_caught_up(schedule, seeded_session):
    assert nightly.fire_if_due(datetime(2026, 10, 8, 9, 30)) is None
    assert nightly.fire_if_due(datetime(2026, 10, 8, 9, 31)) is None
    assert _nightly_requests(seeded_session) == []

    assert nightly.fire_if_due(datetime(2026, 10, 9, 7, 0, 5)) is not None


def test_a_run_another_instance_submitted_for_the_slot_is_not_repeated(schedule, seeded_session):
    nightly.fire_if_due(datetime(2026, 10, 8, 6, 59))
    seeded_session.add(RunRequest(request_source=RequestSource.NIGHTLY, requested_by="nightly",
                                  depth=Depth.FULL, created_at=datetime(2026, 10, 8, 7, 0, 1)))
    seeded_session.commit()

    assert nightly.fire_if_due(datetime(2026, 10, 8, 7, 0, 5)) is None
    assert len(_nightly_requests(seeded_session)) == 1


@pytest.mark.parametrize("cron, status", [("", "UNPAUSED"), ("0 7 * * *", "PAUSED"), ("0 7 * * *", "paused")])
def test_an_empty_or_paused_schedule_never_fires(schedule, seeded_session, cron, status):
    schedule.NIGHTLY_CRON, schedule.NIGHTLY_SCHEDULE_STATUS = cron, status
    nightly.fire_if_due(datetime(2026, 10, 8, 6, 59))

    assert nightly.fire_if_due(datetime(2026, 10, 8, 7, 0, 5)) is None
    assert _nightly_requests(seeded_session) == []


@pytest.mark.parametrize("cron", ["0 0 7 * * ?", "not a cron"])
def test_a_quartz_or_malformed_cron_turns_the_nightly_run_off_and_says_why(schedule, seeded_session, caplog, cron):
    schedule.NIGHTLY_CRON = cron
    with caplog.at_level(logging.ERROR, logger="app.services.nightly"):
        nightly.fire_if_due(datetime(2026, 10, 8, 6, 59))
        assert nightly.fire_if_due(datetime(2026, 10, 8, 7, 0, 5)) is None

    assert _nightly_requests(seeded_session) == []
    assert caplog.text.count("five-field cron") == 1


def test_the_nightly_trigger_is_audited_like_any_other(schedule, seeded_session):
    nightly.fire_if_due(datetime(2026, 10, 8, 6, 59))
    request_id = nightly.fire_if_due(datetime(2026, 10, 8, 7, 0, 5))

    seeded_session.expire_all()
    [event] = seeded_session.exec(select(AuditEvent).where(AuditEvent.entity_id == request_id)).all()
    assert (event.action, event.actor, event.request_source) == ("run.trigger", "nightly", "NIGHTLY")
