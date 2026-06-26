"""Shared pytest fixtures: isolated SQLite DB + seeded TestClient."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

os.environ["TESTING"] = "1"
os.environ["SEED_ON_STARTUP"] = "0"


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    # Reset cached settings/engine so the new URL takes effect.
    from app.core import config as cfg

    cfg.get_settings.cache_clear()
    cfg.settings = cfg.get_settings()
    import app.db.session as sess

    sess._engine = None
    yield path


@pytest.fixture()
def session(db_path):
    from app.db.session import create_all, session_scope

    create_all()
    with session_scope() as s:
        yield s


@pytest.fixture()
def seeded_session(session):
    from app.services.seed import seed_all

    seed_all(session, with_sample_runs=False)
    return session


@pytest.fixture()
def client(db_path):
    from fastapi.testclient import TestClient

    from app.db.session import create_all, session_scope
    from app.main import app
    from app.services.seed import seed_all

    create_all()
    with session_scope() as s:
        seed_all(s, with_sample_runs=False)
    with TestClient(app) as c:
        yield c
