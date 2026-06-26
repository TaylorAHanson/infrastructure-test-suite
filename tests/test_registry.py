"""Registry resolves live executors and falls back to a clean stub."""
from __future__ import annotations


def test_builtin_executors_registered():
    from app.executors import registry

    for key in ("sql_warehouse_smoke", "lakebase_smoke", "outbound_http", "remote_agent"):
        assert key in registry, f"{key} should be registered"


def test_unknown_key_falls_back_to_stub():
    from app.executors import get_executor
    from app.executors.builtin.stubs import CapabilityGatedStub

    ex = get_executor("does_not_exist_kind")
    assert isinstance(ex, CapabilityGatedStub)


def test_every_seeded_definition_resolves(seeded_session):
    from app.executors import get_executor
    from app.models import TestDefinition
    from sqlmodel import select

    defs = seeded_session.exec(select(TestDefinition)).all()
    assert len(defs) >= 20
    for d in defs:
        assert get_executor(d.executor_key) is not None
