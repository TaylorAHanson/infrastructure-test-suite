"""/admin/settings is fetched by the app shell on every page load: it must count
rows in SQL (never load them) and serve the sidebar's environment + mode."""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlmodel import select

_COUNTED_TABLES = re.compile(r"\bFROM (checks|tests|suites|targets|check_runs)\b", re.IGNORECASE)


def _add_check_runs(n: int) -> None:
    from app.db.session import session_scope
    from app.models import Check, CheckRun

    with session_scope() as s:
        check_id = s.exec(select(Check.id)).first()
        for _ in range(n):
            s.add(CheckRun(check_id=check_id, gating=True))
        s.commit()


@pytest.fixture()
def no_workspace(monkeypatch):
    """No Databricks App binding and no local profile; counts WorkspaceClient builds."""
    import app.clients.databricks as databricks

    built: list[int] = []

    def _unavailable():
        built.append(1)
        raise RuntimeError("no profile")

    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    monkeypatch.setattr(databricks, "_cached_context", None)
    monkeypatch.setattr(databricks, "get_workspace_client", _unavailable)
    return built


def test_catalog_counts_match_the_tables(client, no_workspace):
    from app.db.session import session_scope
    from app.models import Check, CheckRun, Suite, Target, Test

    _add_check_runs(7)
    body = client.get("/api/v1/admin/settings").json()

    with session_scope() as s:
        expected = {
            "checks": len(s.exec(select(Check)).all()),
            "tests": len(s.exec(select(Test)).all()),
            "suites": len(s.exec(select(Suite)).all()),
            "targets": len(s.exec(select(Target)).all()),
            "check_runs": len(s.exec(select(CheckRun)).all()),
        }
    assert body["catalog_counts"] == expected
    assert body["catalog_counts"]["check_runs"] == 7
    assert body["catalog_counts"]["checks"] > 0


def test_catalog_counts_are_counted_in_sql_not_loaded(client, no_workspace):
    from app.db.session import get_engine

    _add_check_runs(3)
    statements: list[str] = []

    def _capture(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        assert client.get("/api/v1/admin/settings").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    counted = [s for s in statements if _COUNTED_TABLES.search(s)]
    assert len(counted) == 5, counted
    assert all("count(" in s.lower() for s in counted), counted


def test_settings_carry_the_environment_the_shell_shows(client, no_workspace, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ENVIRONMENT", "staging")
    body = client.get("/api/v1/admin/settings").json()
    assert body["environment"] == "staging"


def test_demo_mode_without_a_workspace_and_resolved_once(client, no_workspace):
    first = client.get("/api/v1/admin/settings").json()
    second = client.get("/api/v1/admin/settings").json()

    assert first["feature_flags"] == {"demo_mode": True}
    assert second["feature_flags"] == {"demo_mode": True}
    assert len(no_workspace) == 1


def test_live_mode_when_a_workspace_profile_resolves(client, monkeypatch):
    import app.clients.databricks as databricks

    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    monkeypatch.setattr(databricks, "_cached_context", None)
    monkeypatch.setattr(
        databricks, "get_workspace_client",
        lambda: SimpleNamespace(config=SimpleNamespace(host="https://ws.example", azure_environment=None)),
    )
    body = client.get("/api/v1/admin/settings").json()
    assert body["feature_flags"] == {"demo_mode": False}


def test_live_mode_inside_a_databricks_app_even_before_a_client_builds(no_workspace, monkeypatch):
    from app.api.v1 import admin

    monkeypatch.setenv("DATABRICKS_APP_NAME", "validation-platform")
    assert admin._demo_mode() is False
    assert no_workspace == []
