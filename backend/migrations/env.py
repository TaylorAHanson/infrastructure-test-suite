"""Alembic environment.

Resolves the database URL from app settings so migrations target SQLite locally
and Lakebase Postgres on Databricks. For Lakebase, the OAuth token is injected
via the same ``do_connect`` listener the app uses.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402
import app.models  # noqa: F401,E402  register all tables

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url())
target_metadata = SQLModel.metadata


def _maybe_attach_token(engine) -> None:
    if not settings.using_postgres:
        return
    from app.db.session import _fresh_lakebase_token

    @event.listens_for(engine, "do_connect")
    def _inject(dialect, conn_rec, cargs, cparams):  # noqa: ANN001
        cparams["password"] = _fresh_lakebase_token()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    _maybe_attach_token(connectable)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
