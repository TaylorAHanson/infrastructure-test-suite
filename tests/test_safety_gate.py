"""Service-layer seam for the safety gate, role guard, and per-target identity.

Spec #20 Testing Decisions — the exhaustive class×tier matrix is awkward
end-to-end, so it is unit-tested directly here; the OAuth M2M token acquisition
(including the ``assume_group`` branch) and the role-registration class guard
are exercised against stubs (no real secrets, no workspace).
"""
from __future__ import annotations

import pytest

from app.clients import databricks as dbx
from app.core.enums import RequestSource, SafetyClass
from app.services.roles import RoleRegistrationError, find_role, validate_roles_for_class
from app.services.safety import (
    SafetyOverride,
    evaluate_operation_safety,
    is_machine_source,
)

R0 = {"service": "jobs", "method": "list"}                 # GET  → R0
R1 = {"service": "clusters", "method": "create"}           # POST → R1
R2 = {"service": "clusters", "method": "delete"}           # DELETE → R2
BLOCKED = {"service": "workspaces", "method": "delete"}     # blocklisted → R3


# --------------------------- the class×tier matrix ---------------------------
@pytest.mark.parametrize("safety_class,op,expect_allowed", [
    # read-only caps at R0.
    (SafetyClass.READ_ONLY, R0, True),
    (SafetyClass.READ_ONLY, R1, False),
    (SafetyClass.READ_ONLY, R2, False),
    # prod-for-us caps at R1 (R2 only via override).
    (SafetyClass.PROD_FOR_US, R0, True),
    (SafetyClass.PROD_FOR_US, R1, True),
    (SafetyClass.PROD_FOR_US, R2, False),
    # sandbox caps at R2.
    (SafetyClass.SANDBOX, R0, True),
    (SafetyClass.SANDBOX, R1, True),
    (SafetyClass.SANDBOX, R2, True),
])
def test_gate_matrix_without_override(safety_class, op, expect_allowed):
    decision = evaluate_operation_safety(op, safety_class)
    assert decision.allowed is expect_allowed
    if not expect_allowed:
        assert decision.status.value == "BLOCKED_BY_SAFETY"


def test_r3_blocklisted_refused_on_every_class():
    # The R3-never floor is identity- and class-independent.
    for sc in SafetyClass:
        decision = evaluate_operation_safety(BLOCKED, sc)
        assert not decision.allowed
        assert decision.layer == "blocklist"
    # ... and an override cannot lift it either.
    decision = evaluate_operation_safety(
        BLOCKED, SafetyClass.PROD_FOR_US,
        override=SafetyOverride(target_name="t"), request_source=RequestSource.UI_MANUAL,
        target_name="t")
    assert not decision.allowed and decision.layer == "blocklist"


def test_unregistered_target_fails_closed():
    assert not evaluate_operation_safety(R0, None).allowed


# ------------------------------- the override --------------------------------
def _override(name="prod-1"):
    return SafetyOverride(target_name=name, reason="incident recovery", confirmed=True)


def test_override_authorizes_one_elevated_tier_on_prod_for_us():
    decision = evaluate_operation_safety(
        R2, SafetyClass.PROD_FOR_US, override=_override(),
        request_source=RequestSource.UI_MANUAL, target_name="prod-1")
    assert decision.allowed and decision.overridden and decision.layer == "override"


def test_override_rejected_for_machine_identity():
    for src in (RequestSource.CI, RequestSource.NIGHTLY, RequestSource.SCHEDULE, RequestSource.API):
        decision = evaluate_operation_safety(
            R2, SafetyClass.PROD_FOR_US, override=_override(),
            request_source=src, target_name="prod-1")
        assert not decision.allowed, f"{src} must not be able to override"


def test_override_only_on_prod_for_us_not_read_only():
    # An override can never widen a read-only target (R1 is 2 tiers over its cap).
    decision = evaluate_operation_safety(
        R1, SafetyClass.READ_ONLY, override=_override(),
        request_source=RequestSource.UI_MANUAL, target_name="prod-1")
    assert not decision.allowed


def test_override_requires_matching_typed_confirmation():
    decision = evaluate_operation_safety(
        R2, SafetyClass.PROD_FOR_US, override=SafetyOverride(target_name="WRONG"),
        request_source=RequestSource.UI_MANUAL, target_name="prod-1")
    assert not decision.allowed


def test_is_machine_source():
    assert is_machine_source(RequestSource.CI)
    assert not is_machine_source(RequestSource.UI_MANUAL)


# --------------------------- role-registration guard -------------------------
def test_role_within_class_is_accepted():
    roles = [{"group_id": "g-analyst", "label": "analyst", "safety_class": "read-only"}]
    stored = validate_roles_for_class(roles, SafetyClass.READ_ONLY)
    assert stored[0]["group_id"] == "g-analyst"
    assert find_role(stored, "analyst").group_id == "g-analyst"


def test_role_exceeding_class_is_rejected():
    roles = [{"group_id": "g-admin", "label": "admin", "safety_class": "sandbox"}]
    with pytest.raises(RoleRegistrationError):
        validate_roles_for_class(roles, SafetyClass.READ_ONLY)


def test_bare_string_role_defaults_to_read_only():
    stored = validate_roles_for_class(["g-x"], SafetyClass.READ_ONLY)
    assert stored[0]["safety_class"] == "read-only"


# ---------------------- OAuth M2M token + assume_group -----------------------
def _stub_secret_reader(scope, key):
    # scope/key holds the client secret; the client id is a sibling secret.
    return f"{scope}:{key}:value"


def test_token_request_omits_assume_group_for_the_sp_path():
    req = dbx.build_token_request("client-abc")
    assert req["grant_type"] == "client_credentials"
    assert "assume_group" not in req


def test_token_request_carries_assume_group_for_run_as_role():
    req = dbx.build_token_request("client-abc", assume_group="grp-analyst")
    assert req["assume_group"] == "grp-analyst"


def test_acquire_target_token_uses_assume_group_branch():
    captured = {}

    def poster(url, data, auth):
        captured["data"] = data
        captured["url"] = url
        return {"access_token": "tok-123", "expires_in": 3600}

    dbx.clear_token_cache()
    # SP path: no assume_group in the request.
    sp = dbx.acquire_target_token("kv/prod-sp", "https://ws", None,
                                  secret_reader=_stub_secret_reader, token_poster=poster)
    assert sp.access_token == "tok-123"
    assert "assume_group" not in captured["data"]
    assert captured["url"].endswith("/oidc/v1/token")

    # run-as-role path: the same identity, now assuming a group.
    dbx.clear_token_cache()
    role_token = dbx.acquire_target_token("kv/prod-sp", "https://ws", "grp-analyst",
                                          secret_reader=_stub_secret_reader, token_poster=poster)
    assert captured["data"]["assume_group"] == "grp-analyst"
    assert role_token.assumed_group == "grp-analyst"


def test_token_is_cached_per_identity_and_role_never_persisted():
    calls = {"n": 0}

    def poster(url, data, auth):
        calls["n"] += 1
        return {"access_token": f"tok-{calls['n']}", "expires_in": 3600}

    dbx.clear_token_cache()
    a1 = dbx.acquire_target_token("kv/sp", "https://ws", None,
                                  secret_reader=_stub_secret_reader, token_poster=poster)
    a2 = dbx.acquire_target_token("kv/sp", "https://ws", None,
                                  secret_reader=_stub_secret_reader, token_poster=poster)
    assert a1.access_token == a2.access_token  # cached, not re-minted
    # A distinct assumed role mints a distinct token.
    b = dbx.acquire_target_token("kv/sp", "https://ws", "grp",
                                 secret_reader=_stub_secret_reader, token_poster=poster)
    assert b.access_token != a1.access_token
    assert calls["n"] == 2


def test_run_engine_mints_token_with_assume_group_for_run_as_role(monkeypatch):
    # When a workspace is bound, the run engine resolves identity to
    # (target, run-as-role?) and mints a token carrying assume_group (ADR-0018).
    from app.core.enums import SafetyClass
    from app.models import Target
    from app.services import run_service

    captured = {}

    def fake_acquire(identity_handle, host, assume_group):
        captured["handle"] = identity_handle
        captured["assume_group"] = assume_group

        class _T:
            access_token = "tok-run"
        return _T()

    monkeypatch.setattr(run_service, "find_role",
                        lambda roles, ref: type("R", (), {"group_id": "grp-analyst"})())
    monkeypatch.setattr("app.clients.databricks.databricks_available", lambda: True)
    monkeypatch.setattr("app.clients.databricks.acquire_target_token", fake_acquire)

    target = Target(name="t", safety_class=SafetyClass.READ_ONLY, identity_handle="kv/sp",
                    host="https://ws")
    token, group = run_service._resolve_identity_token(target, "analyst")
    assert token == "tok-run"
    assert group == "grp-analyst"
    assert captured["assume_group"] == "grp-analyst" and captured["handle"] == "kv/sp"


def test_run_engine_resolves_sp_identity_without_role(monkeypatch):
    from app.core.enums import SafetyClass
    from app.models import Target
    from app.services import run_service

    captured = {}

    def fake_acquire(identity_handle, host, assume_group):
        captured["assume_group"] = assume_group

        class _T:
            access_token = "tok-sp"
        return _T()

    monkeypatch.setattr("app.clients.databricks.databricks_available", lambda: True)
    monkeypatch.setattr("app.clients.databricks.acquire_target_token", fake_acquire)
    target = Target(name="t", safety_class=SafetyClass.READ_ONLY, identity_handle="kv/sp")
    token, group = run_service._resolve_identity_token(target, None)
    assert token == "tok-sp" and group is None
    assert captured["assume_group"] is None


def test_run_engine_skips_minting_in_demo_mode():
    from app.core.enums import SafetyClass
    from app.models import Target
    from app.services import run_service

    # No workspace binding (demo/test default) → resolution only, no token.
    target = Target(name="t", safety_class=SafetyClass.READ_ONLY, identity_handle="kv/sp")
    token, group = run_service._resolve_identity_token(target, None)
    assert token is None


def test_probe_identity_rejects_malformed_handle():
    assert not dbx.probe_identity(None).ok
    assert not dbx.probe_identity("no-slash").ok
    assert not dbx.probe_identity("too/many/parts").ok
    # A well-formed handle passes in demo mode (no live workspace binding).
    assert dbx.probe_identity("kv/sp").ok


def test_probe_identity_reports_token_failure():
    def failing_poster(url, data, auth):
        raise RuntimeError("invalid_client")

    dbx.clear_token_cache()  # don't let a cached token satisfy the probe
    result = dbx.probe_identity("kv/sp", "https://ws",
                                secret_reader=_stub_secret_reader, token_poster=failing_poster)
    assert not result.ok and "invalid_client" in result.message
