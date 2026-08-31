"""The generic ``databricks_sdk`` handler: real execution outside demo mode.

Outside demo mode the handler must perform the Check's operation as the run's
resolved per-target identity — not simulate a PASS (issue #80). These tests
cover: real dispatch bound to ``ctx.token``, the permission-denied path (and
its counterfactual consumption), demo mode staying simulated, and cleanup of a
synthetic artifact via the ``cleanup`` param.
"""
from __future__ import annotations

from databricks.sdk.errors import PermissionDenied

from app.core.enums import Depth, ProbeOutcome
from app.core.operations import Operation
from app.executors.base import HandlerContext
from app.executors.builtin import generic
from app.executors.builtin.generic import DatabricksSdkHandler
from app.models import Target
from app.services.assertion_compiler import evaluate_predicate


class _FakeService:
    def __init__(self, **methods):
        for name, fn in methods.items():
            setattr(self, name, fn)


class _FakeUser:
    user_name = "sp-under-test"


class _FakeClient:
    def __init__(self, **services):
        self.current_user = _FakeService(me=lambda: _FakeUser())
        for name, svc in services.items():
            setattr(self, name, svc)


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def as_dict(self):
        return self._data


def _ctx(*, token=None, target=None, params=None, workspace_client_factory=None):
    params = params or {}
    return HandlerContext(
        run_id="r1",
        operation=Operation.from_dict({"service": "clusters", "method": "list", "params": params}),
        target=target,
        depth=Depth.FULL,
        params=params,
        demo_mode=False,
        token=token,
        workspace_client_factory=workspace_client_factory,
    )


def test_real_operate_dispatches_the_call_as_the_resolved_identity(monkeypatch):
    seen = {}

    def fake_make_client(host, token):
        seen["host"] = host
        seen["token"] = token
        return _FakeClient(clusters=_FakeService(list=lambda: [_FakeResponse({"cluster_id": "c-1"})]))

    monkeypatch.setattr(generic, "_make_workspace_client", fake_make_client)

    def poison_factory():
        raise AssertionError("ambient factory must not be used when a per-target token is resolved")

    target = Target(name="t1", host="https://ws.example.com")
    ctx = _ctx(token="secret-token", target=target, workspace_client_factory=poison_factory)

    steps = DatabricksSdkHandler().run(ctx)

    assert seen == {"host": "https://ws.example.com", "token": "secret-token"}
    operate = next(s for s in steps if s.stage == "OPERATE")
    assert operate.outcome == ProbeOutcome.PASS
    assert operate.evidence["response"] == [{"cluster_id": "c-1"}]
    assert operate.evidence["row_count"] == 1


def test_real_operate_falls_back_to_ambient_factory_without_a_token(monkeypatch):
    def fail_make_client(host, token):
        raise AssertionError("must not mint a per-target client with no token")

    monkeypatch.setattr(generic, "_make_workspace_client", fail_make_client)

    client = _FakeClient(clusters=_FakeService(list=lambda: [_FakeResponse({"cluster_id": "c-2"})]))
    ctx = _ctx(token=None, workspace_client_factory=lambda: client)

    steps = DatabricksSdkHandler().run(ctx)

    operate = next(s for s in steps if s.stage == "OPERATE")
    assert operate.outcome == ProbeOutcome.PASS
    assert operate.evidence["row_count"] == 1


def test_permission_denied_fails_operate_with_the_permission_denied_bucket(monkeypatch):
    def fake_make_client(host, token):
        def _deny(**kwargs):
            raise PermissionDenied("not granted")

        return _FakeClient(clusters=_FakeService(list=_deny))

    monkeypatch.setattr(generic, "_make_workspace_client", fake_make_client)

    ctx = _ctx(token="tok")
    steps = DatabricksSdkHandler().run(ctx)

    operate = next(s for s in steps if s.stage == "OPERATE")
    assert operate.outcome == ProbeOutcome.FAIL
    assert operate.failure_bucket == "PERMISSION_DENIED"


def test_counterfactual_expectation_passes_on_a_real_denial(monkeypatch):
    """A Check expecting denial must still pass when the real call is denied."""
    def fake_make_client(host, token):
        def _deny(**kwargs):
            raise PermissionDenied("not granted")

        return _FakeClient(clusters=_FakeService(list=_deny))

    monkeypatch.setattr(generic, "_make_workspace_client", fake_make_client)

    ctx = _ctx(token="tok")
    steps = DatabricksSdkHandler().run(ctx)

    evidence = {}
    for sr in steps:
        evidence.update(sr.evidence)
    operation_succeeded = not any(sr.failed for sr in steps)

    assert evaluate_predicate({"kind": "denied"}, evidence, operation_succeeded) is True


def test_unknown_sdk_operation_is_service_unsupported(monkeypatch):
    def fake_make_client(host, token):
        return _FakeClient()  # no 'clusters' attribute at all

    monkeypatch.setattr(generic, "_make_workspace_client", fake_make_client)

    ctx = _ctx(token="tok")
    steps = DatabricksSdkHandler().run(ctx)

    operate = next(s for s in steps if s.stage == "OPERATE")
    assert operate.outcome == ProbeOutcome.FAIL
    assert operate.failure_bucket == "SERVICE_UNSUPPORTED"


def test_demo_mode_still_simulates_and_never_touches_the_sdk(monkeypatch):
    def poison(*args, **kwargs):
        raise AssertionError("demo mode must never touch the real SDK")

    monkeypatch.setattr(generic, "_make_workspace_client", poison)

    ctx = HandlerContext(
        run_id="r2",
        operation=Operation.from_dict({"service": "clusters", "method": "list"}),
        depth=Depth.FULL,
        params={"demo_evidence": {"row_count": 3}},
        demo_mode=True,
        workspace_client_factory=poison,
    )
    steps = DatabricksSdkHandler().run(ctx)

    operate = next(s for s in steps if s.stage == "OPERATE")
    assert operate.outcome == ProbeOutcome.PASS
    assert operate.evidence["row_count"] == 3


def test_cleanup_tears_down_the_created_artifact_via_the_cleanup_param(monkeypatch):
    calls = []

    def fake_make_client(host, token):
        clusters = _FakeService(
            create=lambda **kw: _FakeResponse({"cluster_id": "c-99"}),
            permanent_delete=lambda **kw: calls.append(kw),
        )
        return _FakeClient(clusters=clusters)

    monkeypatch.setattr(generic, "_make_workspace_client", fake_make_client)

    ctx = HandlerContext(
        run_id="r3",
        operation=Operation.from_dict({"service": "clusters", "method": "create"}),
        depth=Depth.FULL,
        params={"cleanup": {"service": "clusters", "method": "permanent_delete",
                            "params": {"cluster_id": "$response.cluster_id"}}},
        demo_mode=False,
        token="tok",
    )
    steps = DatabricksSdkHandler().run(ctx)

    assert calls == [{"cluster_id": "c-99"}]
    cleanup_result = steps[-1]
    assert cleanup_result.outcome == ProbeOutcome.PASS


def test_cleanup_is_skipped_in_demo_mode(monkeypatch):
    def poison(*args, **kwargs):
        raise AssertionError("demo mode must never touch the real SDK")

    monkeypatch.setattr(generic, "_make_workspace_client", poison)

    ctx = HandlerContext(
        run_id="r4",
        operation=Operation.from_dict({"service": "clusters", "method": "create"}),
        depth=Depth.FULL,
        params={
            "demo_evidence": {"cluster_id": "c-1"},
            "cleanup": {"service": "clusters", "method": "permanent_delete",
                        "params": {"cluster_id": "$response.cluster_id"}},
        },
        demo_mode=True,
        workspace_client_factory=poison,
    )
    steps = DatabricksSdkHandler().run(ctx)
    assert not any(s.stage == "VERIFY" and "Cleaned up" in s.message for s in steps)


def test_cleanup_is_skipped_when_operate_failed():
    ctx = HandlerContext(
        run_id="r5",
        operation=Operation.from_dict({"service": "clusters", "method": "create"}),
        depth=Depth.FULL,
        params={
            "demo_outcome": "fail",
            "cleanup": {"service": "clusters", "method": "permanent_delete", "params": {}},
        },
        demo_mode=True,
    )
    steps = DatabricksSdkHandler().run(ctx)
    assert not any(s.stage == "VERIFY" and "Cleaned up" in s.message for s in steps)
