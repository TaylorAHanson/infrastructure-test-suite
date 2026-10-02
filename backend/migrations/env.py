"""Alembic environment.

The app migrates at startup (``app.db.migrate``) and hands over its connection
in ``config.attributes["connection"]``. Run from the CLI, this uses the app's
engine, so migrations target SQLite locally and Lakebase Postgres on Databricks
with the same OAuth token injection and ``search_path`` the app uses.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.core.config import get_settings  # noqa: E402
from app.db.migrate import version_table_schema  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402
import app.models  # noqa: F401,E402  register all tables

config = context.config
# Only the CLI configures logging; the app has its own.
if config.config_file_name is not None and "connection" not in config.attributes:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().sqlalchemy_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:  # noqa: ANN001
    context.configure(connection=connection, target_metadata=target_metadata,
                      version_table_schema=version_table_schema(connection))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    from app.db.session import get_engine

    with get_engine().connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
