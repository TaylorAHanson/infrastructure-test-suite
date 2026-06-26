"""SQLAlchemy/SQLModel engine + session management.

Local dev uses SQLite. Databricks uses Lakebase Postgres, where the password is
a short-lived OAuth token. We inject a fresh token on every physical connection
via a ``do_connect`` event listener and cache it briefly, so long-running app
processes survive token rotation without restarts.
"""
from __future__ import annotations

import logging
import time
import threading
from typing import Iterator, Optional

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None
_engine_lock = threading.Lock()

# Cache the Lakebase OAuth token; refresh well before the ~1h expiry.
_token_cache: dict[str, float | str] = {"token": "", "expires_at": 0.0}
_TOKEN_TTL_SECONDS = 45 * 60


def _fresh_lakebase_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < float(_token_cache["expires_at"]):
        return str(_token_cache["token"])
    from app.core.config import get_databricks_oauth_token

    token = get_databricks_oauth_token()
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + _TOKEN_TTL_SECONDS
    return token


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine

        url = settings.sqlalchemy_url()
        if url.startswith("sqlite"):
            _engine = create_engine(
                url,
                echo=False,
                connect_args={"check_same_thread": False},
            )
        else:
            _engine = create_engine(
                url,
                echo=False,
                pool_pre_ping=True,
                pool_recycle=1800,
                pool_size=5,
                max_overflow=10,
            )

            @event.listens_for(_engine, "do_connect")
            def _inject_oauth_password(dialect, conn_rec, cargs, cparams):  # noqa: ANN001
                # Lakebase: password is a rotating OAuth token, not a static
                # secret. Inject the freshest token at connect time.
                try:
                    cparams["password"] = _fresh_lakebase_token()
                except Exception as exc:  # pragma: no cover - depends on env
                    logger.error("Failed to obtain Lakebase OAuth token: %s", exc)
                    raise

        logger.info("DB engine initialised (%s)", "postgres" if settings.using_postgres else "sqlite")
        return _engine


def create_all() -> None:
    """Create tables for local/dev convenience.

    Production schema changes go through Alembic migrations; this is a
    safety net so SQLite local dev and first-boot demo mode just work.
    """
    import app.models  # noqa: F401  ensure models are registered

    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session."""
    with Session(get_engine()) as session:
        yield session


def session_scope() -> Session:
    """Return a new session for use outside request scope (poller, jobs)."""
    return Session(get_engine())
