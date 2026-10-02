"""Run-completion webhook — the platform's only push (spec #39, ADR-0016).

Two seams. The **service seam** unit-tests the payload builder, the best-effort
sender (bounded retry, failure logged-not-fatal), the ≥ 1-finding fire gate, and
the audited sink-config edit. The **HTTP/engine seam** drives a real run through
``TestClient`` + SQLite against a **captured/stubbed sink** and asserts the POST
fires on a run with a finding, stays silent on all-green, widens on the toggle,
and that a delivery failure never breaks the run.

Delivery is inline + backoff-free under test (conftest sets
``WEBHOOK_DELIVERY_ASYNC=0`` / ``WEBHOOK_RETRY_BACKOFF_SECONDS=0``), so the
captured sink observes each POST deterministically.
"""
from __future__ import annotations

import httpx
import pytest

from app.core.enums import RequestSource, RunStatus
from app.models import CheckRun, RunRequest, WEBHOOK_CONFIG_ID
from app.services import run_service, webhook
from helpers import script_test


# --------------------------------------------------------------------------
# capture / stub helpers
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _demo(monkeypatch):
    """Never touch a real workspace from these tests, whatever profile is configured."""
    monkeypatch.setattr(run_service, "_demo_mode", lambda: True)


@pytest.fixture()
def sink(monkeypatch):
    """Capture every outbound POST instead of hitting the network."""
    posted: list[dict] = []

    def _fake_post(url, payload, timeout):
        posted.append({"url": url, "payload": payload})

    monkeypatch.setattr(webhook, "_post_json", _fake_post)
    return posted


def _enable_sink(session, urls=("https://hook.example/receiver",),
                 fire_on_all_completions=False):
    webhook.update_config(session, actor="op", urls=list(urls), enabled=True,
                          fire_on_all_completions=fire_on_all_completions)


def _mk_run(session, *, verdict, gating=True, git_sha="abc123",
            event_ref=None, bucket=None, stage=None, source=RequestSource.CI):
    """A RunRequest + one CheckRun in the given terminal state."""
    request = RunRequest(request_source=source, git_sha=git_sha, event_ref=event_ref,
                         status=verdict)
    session.add(request)
    session.commit()
    session.refresh(request)
    run = CheckRun(request_id=request.id, check_id="chk_x", target_id="tgt_1",
                   workspace_name="ws-1", request_source=source, git_sha=git_sha,
                   event_ref=event_ref, verdict=verdict, gating=gating,
                   failure_bucket=bucket, failure_stage=stage)
    session.add(run)
    session.commit()
    return request


# ==========================================================================
# service seam
# ==========================================================================
def test_run_findings_reuses_actionability(session):
    req = _mk_run(session, verdict=RunStatus.FAIL, bucket="OPERATION_FAILED", stage="OPERATE")
    assert [r.verdict for r in webhook.run_findings(session, req.id)] == [RunStatus.FAIL]

    green = _mk_run(session, verdict=RunStatus.PASS)
    assert webhook.run_findings(session, green.id) == []


def test_blocked_by_safety_counts_as_a_finding(session):
    req = _mk_run(session, verdict=RunStatus.BLOCKED_BY_SAFETY,
                  bucket="BLOCKED_BY_SAFETY", stage="AUTHORIZE")
    assert len(webhook.run_findings(session, req.id)) == 1


def test_payload_carries_verdict_bucket_ref_and_deep_link(session):
    req = _mk_run(session, verdict=RunStatus.FAIL, git_sha="deadbeef",
                  bucket="PERMISSION_DENIED", stage="AUTHORIZE")
    fired = webhook.run_findings(session, req.id)
    payload = webhook.build_payload(session, session.get(RunRequest, req.id), fired)

    assert payload["verdict"] == "FAIL"
    assert payload["commit_ref"] == "deadbeef"
    assert payload["finding_count"] == 1
    assert payload["findings"][0]["bucket_at_stage"] == "PERMISSION_DENIED @ AUTHORIZE"
    assert payload["findings"][0]["workspace_name"] == "ws-1"
    assert "PERMISSION_DENIED @ AUTHORIZE" in payload["summary"]
    assert payload["deep_link"].endswith(f"/analytics?run_id={req.id}")
    assert payload["targets"] == [{"target_id": "tgt_1", "workspace_name": "ws-1"}]


def test_payload_carries_non_deploy_event_ref(session):
    req = _mk_run(session, verdict=RunStatus.FAIL, git_sha="unknown",
                  event_ref="metastore grant change · 21:14", source=RequestSource.NIGHTLY,
                  bucket="OPERATION_FAILED", stage="OPERATE")
    payload = webhook.build_payload(session, session.get(RunRequest, req.id),
                                    webhook.run_findings(session, req.id))
    assert payload["event_ref"] == "metastore grant change · 21:14"


def test_deep_link_uses_configured_base_url(session, monkeypatch):
    from app.core import config as cfg
    monkeypatch.setattr(cfg.get_settings(), "WEBHOOK_DEEP_LINK_BASE_URL",
                        "https://vp.example.com/")
    assert webhook._deep_link("req_9") == "https://vp.example.com/analytics?run_id=req_9"


# ---- best-effort delivery ----
def test_deliver_success_records_ok(sink):
    results = webhook.deliver(["https://a/hook"], {"event": "run.completed"})
    assert results == [{"url": "https://a/hook", "ok": True, "attempts": 1}]
    assert len(sink) == 1


def test_delivery_failure_is_logged_not_fatal_with_bounded_retry(monkeypatch):
    calls = {"n": 0}

    def _always_fail(url, payload, timeout):
        calls["n"] += 1
        raise httpx.ConnectError("sink down")

    monkeypatch.setattr(webhook, "_post_json", _always_fail)
    # Never raises; reports the failure after a bounded number of attempts.
    results = webhook.deliver(["https://down/hook"], {"event": "run.completed"})
    assert results[0]["ok"] is False
    assert "sink down" in results[0]["error"]
    assert calls["n"] == webhook.get_settings().WEBHOOK_MAX_RETRIES  # bounded, then gave up


def test_delivery_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def _flaky(url, payload, timeout):
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("transient")

    monkeypatch.setattr(webhook, "_post_json", _flaky)
    results = webhook.deliver(["https://flaky/hook"], {})
    assert results[0] == {"url": "https://flaky/hook", "ok": True, "attempts": 2}


def test_deliver_fans_out_to_every_sink(sink):
    webhook.deliver(["https://a/1", "https://b/2"], {"event": "run.completed"})
    assert {p["url"] for p in sink} == {"https://a/1", "https://b/2"}


# ---- the fire gate ----
def test_emit_fires_on_a_finding(session, sink):
    _enable_sink(session)
    req = _mk_run(session, verdict=RunStatus.FAIL, bucket="OPERATION_FAILED", stage="OPERATE")

    payload = webhook.emit_run_completion(req.id)
    assert payload is not None and payload["finding_count"] == 1
    assert len(sink) == 1
    assert sink[0]["url"] == "https://hook.example/receiver"


def test_emit_is_silent_on_all_green(session, sink):
    _enable_sink(session)
    req = _mk_run(session, verdict=RunStatus.PASS)

    assert webhook.emit_run_completion(req.id) is None
    assert sink == []


def test_toggle_widens_to_every_completion(session, sink):
    _enable_sink(session, fire_on_all_completions=True)
    req = _mk_run(session, verdict=RunStatus.PASS)

    payload = webhook.emit_run_completion(req.id)
    assert payload is not None and payload["finding_count"] == 0
    assert len(sink) == 1


def test_emit_silent_when_disabled(session, sink):
    # Enabled=False by default: even a finding pushes nothing.
    req = _mk_run(session, verdict=RunStatus.FAIL, bucket="X", stage="OPERATE")
    assert webhook.emit_run_completion(req.id) is None
    assert sink == []


def test_emit_silent_for_non_terminal_run(session, sink):
    _enable_sink(session)
    req = _mk_run(session, verdict=RunStatus.RUNNING)
    assert webhook.emit_run_completion(req.id) is None
    assert sink == []


# ---- audited config edit ----
def test_config_defaults_off(session):
    config = webhook.get_config(session)
    assert config.id == WEBHOOK_CONFIG_ID
    assert config.enabled is False and config.urls == []


def test_update_config_is_audited(session):
    from sqlmodel import select
    from app.models import AuditEvent

    webhook.update_config(session, actor="alice",
                          urls=["https://hook/x", "  "], enabled=True)
    config = webhook.get_config(session)
    assert config.enabled is True
    assert config.urls == ["https://hook/x"]  # blank entry trimmed away

    events = session.exec(
        select(AuditEvent).where(AuditEvent.action == "webhook.config.change")
    ).all()
    assert len(events) == 1
    assert events[0].actor == "alice"
    assert events[0].before["enabled"] is False
    assert events[0].after["enabled"] is True


# ==========================================================================
# HTTP / engine seam
# ==========================================================================
def _register_target(client, name, safety_class):
    return client.post("/api/v1/targets", json={
        "name": name, "workspace_name": name, "safety_class": safety_class,
        "identity_handle": "kv/sp",
    }).json()


_R1 = 'w.clusters.create(spark_version="15.4.x-scala2.12", cluster_name="t")'


def _make_test(client, call, name):
    """An Active script Test whose one check makes ``call``."""
    return script_test(client, f'with check("{name}"):\n    {call}\n', name)


def _run(client, test_id, target_id):
    return client.post("/api/v1/runs", json={
        "test_ids": [test_id], "target_ids": [target_id], "depth": "full",
        "request_source": "CI"}).json()["run_id"]


def test_config_endpoint_roundtrip_and_audit(client):
    assert client.get("/api/v1/webhook/config").json()["enabled"] is False

    put = client.put("/api/v1/webhook/config?actor=carol", json={
        "urls": ["https://hook.example/r"], "enabled": True,
        "fire_on_all_completions": True})
    assert put.status_code == 200
    body = put.json()
    assert body["enabled"] is True and body["urls"] == ["https://hook.example/r"]
    assert body["fire_on_all_completions"] is True

    # The sink edit is an audited config-plane mutation, visible in the audit view.
    events = client.get("/api/v1/audit", params={"event_type": "webhook.config.change"}).json()
    assert len(events) == 1 and events[0]["actor"] == "carol"
    # And it is one of the config-plane event types the view offers as a filter.
    assert "webhook.config.change" in client.get("/api/v1/audit/event-types").json()


def test_run_with_finding_fires_one_post(client, sink):
    client.put("/api/v1/webhook/config", json={
        "urls": ["https://hook.example/r"], "enabled": True})
    # An R1 mutation on a read-only target is BLOCKED_BY_SAFETY — a finding.
    ro = _register_target(client, "ws-ro", "read-only")
    test = _make_test(client, _R1, "blocked")
    run_id = _run(client, test["id"], ro["id"])

    assert len(sink) == 1
    payload = sink[0]["payload"]
    assert payload["run_id"] == run_id
    assert payload["verdict"] == "BLOCKED_BY_SAFETY"
    assert payload["finding_count"] >= 1
    assert payload["deep_link"].endswith(f"/analytics?run_id={run_id}")


def test_all_green_run_is_silent(client, sink):
    client.put("/api/v1/webhook/config", json={
        "urls": ["https://hook.example/r"], "enabled": True})
    ro = _register_target(client, "ws-ok", "read-only")
    test = _make_test(client, "w.jobs.list()", "green")
    _run(client, test["id"], ro["id"])
    assert sink == []


def test_delivery_failure_does_not_break_the_run(client, monkeypatch):
    def _boom(url, payload, timeout):
        raise httpx.ConnectError("receiver down")

    monkeypatch.setattr(webhook, "_post_json", _boom)
    client.put("/api/v1/webhook/config", json={
        "urls": ["https://down/hook"], "enabled": True})
    ro = _register_target(client, "ws-fail", "read-only")
    test = _make_test(client, _R1, "blocked2")
    run_id = _run(client, test["id"], ro["id"])

    # The run still reaches its terminal verdict — the webhook failure is swallowed.
    resource = client.get(f"/api/v1/runs/{run_id}").json()
    assert resource["phase"] == "terminal"
    assert resource["verdict"] == "BLOCKED_BY_SAFETY"
