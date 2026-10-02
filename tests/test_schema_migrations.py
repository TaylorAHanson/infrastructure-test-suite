"""Startup migrations: a schema is upgraded, built, adopted or reported."""
from __future__ import annotations

import logging
import shutil

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlmodel import SQLModel, create_engine

import app.models  # noqa: F401
from app.db import migrate
from app.db.migrate import ALEMBIC_INI, migrate_schema

_REVISION = '''
import sqlalchemy as sa
from alembic import op

revision = "{rev}"
down_revision = {down!r}
branch_labels = None
depends_on = None


def upgrade():
    {body}


def downgrade():
    pass
'''


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema.db'}")
    yield engine
    engine.dispose()


def _scripts(ini=ALEMBIC_INI) -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ini)))


def _revision(engine):
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _stamp(engine, revision, ini=ALEMBIC_INI) -> None:
    with engine.begin() as conn:
        MigrationContext.configure(conn).stamp(_scripts(ini), revision)


def test_an_empty_database_is_built_from_the_models_and_tracked_at_head(engine):
    assert migrate_schema(engine) == "created"

    assert set(SQLModel.metadata.tables) <= set(inspect(engine).get_table_names())
    assert _revision(engine) == _scripts().get_current_head()


def test_a_database_at_head_is_left_alone(engine):
    migrate_schema(engine)

    assert migrate_schema(engine) == "current"
    assert _revision(engine) == _scripts().get_current_head()


def test_a_database_behind_head_runs_the_migrations_it_is_missing(engine, tmp_path, monkeypatch):
    scripts = tmp_path / "migrations"
    (scripts / "versions").mkdir(parents=True)
    shutil.copy(ALEMBIC_INI.parent / "migrations" / "env.py", scripts / "env.py")
    (scripts / "versions" / "r1.py").write_text(_REVISION.format(rev="r1", down=None, body="pass"))
    (scripts / "versions" / "r2.py").write_text(_REVISION.format(
        rev="r2", down="r1", body='op.create_table("probe", sa.Column("id", sa.Integer, primary_key=True))'))
    ini = tmp_path / "alembic.ini"
    ini.write_text(f"[alembic]\nscript_location = {scripts}\n")
    monkeypatch.setattr(migrate, "ALEMBIC_INI", ini)
    SQLModel.metadata.create_all(engine)
    _stamp(engine, "r1", ini)

    assert migrate_schema(engine) == "upgraded"

    assert _revision(engine) == "r2"
    assert "probe" in inspect(engine).get_table_names()


def test_an_untracked_database_that_matches_the_models_is_tracked_at_head(engine):
    SQLModel.metadata.create_all(engine)

    assert migrate_schema(engine) == "stamped"
    assert _revision(engine) == _scripts().get_current_head()


def test_an_untracked_database_missing_a_nullable_column_is_still_tracked(engine):
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE tests DROP COLUMN script")

    assert migrate_schema(engine) == "stamped"


def test_an_untracked_database_missing_a_required_column_is_reported_and_left_untracked(engine, caplog):
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE suites RENAME TO suites_old")
        conn.exec_driver_sql("CREATE TABLE suites (id VARCHAR PRIMARY KEY)")

    with caplog.at_level(logging.ERROR, logger="app.db.migrate"):
        assert migrate_schema(engine) == "untracked"

    assert _revision(engine) is None
    assert "suites.name is missing" in caplog.text
