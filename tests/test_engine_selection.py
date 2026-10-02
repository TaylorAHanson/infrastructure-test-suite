"""A submit that names nothing runnable is a 400, never an empty (or partial) run.

Unknown Suite/Test ids, a Suite with no Tests and Tests that are all
Draft/disabled are refused up front; empty selection lists still mean "all".
"""
from __future__ import annotations

import pytest

from app.db.session import session_scope
from app import models
from app.models import Suite
from app.services import run_service

from helpers import script_test
from helpers import target_id as _target

READS = 'with check("lists jobs"):\n    w.jobs.list()\n'


@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


def _submit(client, **selection):
    return client.post("/api/v1/runs", json={"target_ids": [_target(client, "read-only")], **selection})


def test_selection_errors_reuse_the_unknown_target_error():
    assert issubclass(run_service.UnknownSelectionError, run_service.UnknownTargetError)


def test_unknown_suite_or_test_ids_are_a_400(client):
    for key, bogus in (("suite_ids", "suite_nope"), ("test_ids", "test_nope")):
        r = _submit(client, **{key: [bogus]})
        assert r.status_code == 400, (key, r.text)
        assert bogus in r.json()["detail"]
    # A real id alongside the unknown one doesn't rescue it.
    r = _submit(client, test_ids=[script_test(client, READS, "Reads")["id"], "test_nope"])
    assert r.status_code == 400 and "test_nope" in r.json()["detail"]


def test_a_suite_with_no_tests_is_a_400(client):
    with session_scope() as s:
        suite = Suite(name="Empty shelf")
        s.add(suite)
        s.commit()
        suite_id = suite.id
    r = _submit(client, suite_ids=[suite_id])
    assert r.status_code == 400
    assert r.json()["detail"] == "suite 'Empty shelf' has no tests"


def test_naming_only_draft_tests_says_to_activate_first(client):
    draft = script_test(client, 'with check("ok"):\n    pass\n', "Fresh draft", active=False)
    assert draft["status"] == "draft"
    r = _submit(client, test_ids=[draft["id"]])
    assert r.status_code == 400
    assert "Draft" in r.json()["detail"] and "first" in r.json()["detail"]

    # Next to a runnable Test, the Draft is just not run (Draft never runs).
    assert _submit(client, test_ids=[draft["id"], script_test(client, READS, "Reads")["id"]]).status_code == 202


def test_naming_only_disabled_tests_is_a_400(client):
    reads = script_test(client, READS, "Reads")["id"]
    with session_scope() as s:
        test = s.get(models.Test, reads)
        test.enabled = False
        s.add(test)
        s.commit()
    r = _submit(client, test_ids=[reads])
    assert r.status_code == 400 and "disabled" in r.json()["detail"]


def test_empty_selection_lists_still_mean_all(client):
    mine = {script_test(client, READS, "Reads")["id"], script_test(client, READS, "Reads again")["id"]}
    r = _submit(client)
    assert r.status_code == 202, r.text
    resource = client.get(f"/api/v1/runs/{r.json()['run_id']}").json()
    ran = {client.get(f"/api/v1/runs/{rid}").json()["test_id"] for rid in resource["run_ids"]}
    assert mine <= ran
