"""Registry + open-SDK operation validation against the blocklist (ADR-0017)."""
from __future__ import annotations

import pytest

from app.core.operations import Operation
from app.handlers import (
    OperationBlocked,
    get_handler,
    is_operation_allowed,
    registry,
    validate_operation,
)
from app.handlers.builtin.generic import UnsupportedHandler


def test_builtin_handlers_registered():
    for key in ("databricks_sdk", "sql_warehouse", "outbound_http", "lakebase"):
        assert key in registry, f"{key} should be registered"


def test_open_sdk_allows_arbitrary_non_blocklisted_call():
    # No allowlist: an ordinary SDK call validates fine (SDK surface − blocklist).
    validate_operation(Operation("clusters", "create"))
    ok, reason = is_operation_allowed(Operation("jobs", "list"))
    assert ok and reason is None
    ok, reason = is_operation_allowed(Operation("dashboards", "delete"))  # R2, not R3
    assert ok


def test_blocklisted_operation_is_refused():
    ok, reason = is_operation_allowed(Operation("workspaces", "delete"))
    assert not ok and "blocklisted" in reason.lower()
    with pytest.raises(OperationBlocked):
        validate_operation(Operation("metastores", "delete"))
    with pytest.raises(OperationBlocked):
        validate_operation({"service": "catalogs", "method": "delete"})


def test_unknown_handler_falls_back_to_stub():
    assert isinstance(get_handler("does_not_exist"), UnsupportedHandler)


def test_every_seeded_check_resolves_a_handler(seeded_session):
    from sqlmodel import select

    from app.models import Check
    from app.services.seed import CHECK_SPECS

    checks = seeded_session.exec(select(Check)).all()
    assert len(checks) >= len(CHECK_SPECS)
    for c in checks:
        assert get_handler(c.handler_key) is not None


def test_seeded_catalog_covers_every_layer_an_apply_can_break(seeded_session):
    """The default catalog reaches identity, Unity Catalog storage, SQL, classic
    and serverless compute, and security posture — not just the control plane."""
    from sqlmodel import select

    from app.models import Suite, Test

    tests = seeded_session.exec(select(Test)).all()
    test_tags = {tag for t in tests for tag in (t.tags or [])}
    for tag in ("identity", "uc", "storage", "sql", "classic", "serverless", "network", "posture"):
        assert tag in test_tags, f"no {tag}-tagged test in the catalog"

    suite_names = {s.name for s in seeded_session.exec(select(Suite)).all()}
    assert {"CI Gate", "Post-apply health: compute & network", "Security posture (drift)"} <= suite_names
