"""Bring the Postgres schema up to date at startup.

Every model change ships with an Alembic migration (``backend/migrations``), and
the deployed app applies them here before it serves anything:

- a schema Alembic tracks is upgraded to head;
- an empty schema is built from the models and stamped at head;
- an untracked schema that already matches the models (built by startups that
  only ran ``create_all``) is stamped at head, so later migrations apply to it;
- an untracked schema that doesn't match is left alone and reported, since
  there's no telling which migrations it's missing.

It all runs in one transaction under an advisory lock, so two replicas starting
together can't both migrate, and a failed migration changes nothing.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Enum, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlmodel import SQLModel

logger = logging.getLogger(__name__)
logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def version_table_schema(conn: Connection) -> Optional[str]:
    """The schema holding ``alembic_version``: the app's own, never ``public``."""
    if conn.dialect.name != "postgresql":
        return None
    from app.db.session import get_db_schema

    return get_db_schema()


def migrate_schema(engine: Engine) -> str:
    """Migrate, build or adopt the schema; returns what it did.

    One of ``current``, ``upgraded``, ``created``, ``stamped`` or ``untracked``.
    """
    import app.models  # noqa: F401  register every table

    with engine.begin() as conn:
        schema = version_table_schema(conn)
        if schema is not None:
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"alembic:{schema}"})
        config = Config(str(ALEMBIC_INI))
        config.attributes["connection"] = conn
        head = ScriptDirectory.from_config(config).get_current_head()
        current = MigrationContext.configure(
            conn, opts={"version_table_schema": schema}).get_current_revision()

        if current == head:
            return "current"
        if current is not None:
            command.upgrade(config, "head")
            logger.info("Migrated the database schema from %s to %s.", current, head)
            return "upgraded"
        if not set(inspect(conn).get_table_names()) & set(SQLModel.metadata.tables):
            SQLModel.metadata.create_all(conn)
            command.stamp(config, "head")
            logger.info("Created the database schema at %s.", head)
            return "created"
        drift = unfixable_drift(conn)
        if drift:
            logger.error(
                "The database schema isn't tracked by Alembic and doesn't match the models, so it "
                "can't be migrated: %s. Point DB_SCHEMA at a new schema, or fix these by hand and "
                "restart.", "; ".join(drift))
            return "untracked"
        command.stamp(config, "head")
        logger.info("Started tracking the existing database schema at %s.", head)
        return "stamped"


def unfixable_drift(conn: Connection) -> list[str]:
    """Differences from the models that the create_all safety net can't repair.

    It adds missing tables and nullable columns, but not a required column or an
    enum label.
    """
    inspector = inspect(conn)
    existing = set(inspector.get_table_names())
    problems: list[str] = []
    enum_columns = []
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing:
            continue
        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in present:
                if column.primary_key or not column.nullable:
                    problems.append(f"{table.name}.{column.name} is missing")
            elif isinstance(column.type, Enum) and column.type.name:
                enum_columns.append(column)
    if conn.dialect.name == "postgresql" and enum_columns:
        problems += _missing_enum_labels(conn, enum_columns)
    return problems


def _missing_enum_labels(conn: Connection, columns: list) -> list[str]:
    labels: dict[str, set[str]] = {}
    for name, label in conn.execute(text(
            "SELECT t.typname, e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = current_schema()")):
        labels.setdefault(name, set()).add(label)
    problems = []
    for name in sorted({c.type.name for c in columns}):
        wanted = next(set(c.type.enums) for c in columns if c.type.name == name)
        if name not in labels:
            problems.append(f"enum type {name} is missing")
        elif wanted - labels[name]:
            problems.append(f"enum {name} lacks {', '.join(sorted(wanted - labels[name]))}")
    return problems
