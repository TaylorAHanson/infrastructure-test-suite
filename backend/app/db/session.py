"""SQLAlchemy/SQLModel engine + session management.

Local dev uses SQLite. Databricks uses Lakebase Postgres, where the password is
a short-lived OAuth token. We inject a fresh token on every physical connection
via a ``do_connect`` event listener and cache it briefly, so long-running app
processes survive token rotation without restarts.
"""
from __future__ import annotations

import logging
import re
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

# Bare SQL identifier (letters/digits/underscore, not starting with a digit).
_SCHEMA_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_DB_SCHEMA = "regression"


def get_db_schema() -> str:
    """Resolve the app's Postgres schema, guarding against injection.

    The value is interpolated into ``search_path`` / ``CREATE SCHEMA`` (which
    can't be parameterized), so anything that isn't a bare identifier falls back
    to the default. We never fall back to ``public`` (PG 15+ revokes CREATE on
    it for non-owner roles, which the app needs).
    """
    schema = (getattr(settings, "DB_SCHEMA", "") or _DEFAULT_DB_SCHEMA).strip()
    if not _SCHEMA_IDENT_RE.match(schema):
        logger.warning("Invalid DB_SCHEMA %r; falling back to '%s'.", schema, _DEFAULT_DB_SCHEMA)
        return _DEFAULT_DB_SCHEMA
    return schema

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
            schema = get_db_schema()
            _engine = create_engine(
                url,
                echo=False,
                pool_pre_ping=True,
                pool_recycle=1800,
                pool_size=5,
                max_overflow=10,
                # search_path is set via libpq connection options (not a SQL
                # `SET`) so it survives the pool's transaction rollback on
                # connection return. Putting the app schema first means
                # create_all() and all queries resolve there, not `public`.
                connect_args={"options": f"-csearch_path={schema},public"},
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

            @event.listens_for(_engine, "connect")
            def _ensure_schema(dbapi_connection, connection_record):  # noqa: ANN001
                # Create the app-owned schema if missing. search_path is already
                # applied via connect_args, so this just guarantees the target
                # exists before create_all()/queries run.
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
                    dbapi_connection.commit()
                except Exception as exc:  # pragma: no cover - depends on env
                    logger.warning("Failed to ensure '%s' schema exists: %s", schema, exc)
                finally:
                    cursor.close()

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
