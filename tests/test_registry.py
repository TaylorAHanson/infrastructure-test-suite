"""Registry + open-SDK operation validation against the blocklist (ADR-0017)."""
from __future__ import annotations

import pytest

from app.core.operations import Operation
from app.executors import (
    OperationBlocked,
    get_handler,
    is_operation_allowed,
    registry,
    validate_operation,
)
from app.executors.builtin.generic import UnsupportedHandler


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


def test_seeded_catalog_covers_producer_processing_consumer(seeded_session):
    """The default catalog covers all three README test-category lanes (issue #61)."""
    from sqlmodel import select

    from app.models import Suite, Test

    tests = seeded_session.exec(select(Test)).all()
    test_tags = {tag for t in tests for tag in (t.tags or [])}
    assert "producer" in test_tags, "No producer-tagged test in catalog"
    assert "processing" in test_tags, "No processing-tagged test in catalog"
    assert "consumer" in test_tags, "No consumer-tagged test in catalog"

    suites = seeded_session.exec(select(Suite)).all()
    suite_names = {s.name for s in suites}
    assert "Producer Suite" in suite_names, "Producer Suite missing from catalog"
    assert "Processing Suite" in suite_names, "Processing Suite missing from catalog"
    assert "Consumer Suite" in suite_names, "Consumer Suite missing from catalog"
