"""structured tests removed: delete their rows and the worked_examples table

Revision ID: wf43legacypurge
Revises: wf42assistantcfg
Create Date: 2026-10-02

Structured Tests and the worked-example library are gone from the app. In one
transaction this deletes every Test without a script and every Check whose
handler isn't ``script`` (plus a script Check left owned by a deleted Test that
no other Test lists), their Test/Suite memberships, version snapshots, CheckRuns
and those runs' StageOutcomes, the RunRequest of a finished run whose every
result was one of those, and the ``worked_examples`` table. Suites are kept. It
writes one ``catalog.upgrade`` audit record with the counts when it deletes
anything. The same deletes run at startup (``app.services.legacy_purge``) for
installs that don't use Alembic; whichever runs second finds nothing.

Downgrade recreates an empty ``worked_examples`` table; deleted rows aren't restored.
"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "wf43legacypurge"
down_revision: Union[str, None] = "wf42assistantcfg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TERMINAL = ("PASS", "FAIL", "ERROR", "BLOCKED_BY_SAFETY", "SKIPPED", "CANCELLED")

checks = sa.table("checks", sa.column("id"), sa.column("handler_key"), sa.column("hints", sa.JSON))
tests = sa.table("tests", sa.column("id"), sa.column("name"), sa.column("script"))
test_checks = sa.table("test_checks", sa.column("test_id"), sa.column("check_id"))
suites = sa.table("suites", sa.column("id"), sa.column("name"))
suite_members = sa.table("suite_members", sa.column("suite_id"), sa.column("test_id"))
check_runs = sa.table("check_runs", sa.column("run_id"), sa.column("request_id"),
                      sa.column("test_id"), sa.column("check_id"))
stage_outcomes = sa.table("stage_outcomes", sa.column("run_id"))
run_requests = sa.table("run_requests", sa.column("id"), sa.column("status"))
entity_versions = sa.table("entity_versions", sa.column("entity_type"), sa.column("entity_id"))
audit_events = sa.table(
    "audit_events", sa.column("id"), sa.column("ts", sa.DateTime), sa.column("actor"),
    sa.column("actor_kind"), sa.column("action"), sa.column("entity_type"), sa.column("entity_id"),
    sa.column("summary"), sa.column("before", sa.JSON), sa.column("after", sa.JSON),
    sa.column("request_source"))


def upgrade() -> None:
    bind = op.get_bind()
    legacy_tests = set(bind.execute(sa.select(tests.c.id).where(
        sa.or_(tests.c.script.is_(None), tests.c.script == ""))).scalars())
    legacy_checks = set(bind.execute(sa.select(checks.c.id).where(checks.c.handler_key != "script")).scalars())
    listed = set(bind.execute(sa.select(test_checks.c.check_id).where(
        test_checks.c.test_id.not_in(legacy_tests))).scalars())
    for check_id, hints in bind.execute(sa.select(checks.c.id, checks.c.hints).where(checks.c.handler_key == "script")):
        if (hints or {}).get("script_test_id") in legacy_tests and check_id not in listed:
            legacy_checks.add(check_id)
    has_examples = sa.inspect(bind).has_table("worked_examples")

    legacy_run = sa.or_(check_runs.c.check_id.in_(legacy_checks),
                        sa.and_(check_runs.c.test_id.is_not(None), check_runs.c.test_id.in_(legacy_tests)))
    emptied_requests = set(bind.execute(sa.select(run_requests.c.id).where(
        run_requests.c.id.in_(sa.select(check_runs.c.request_id).where(legacy_run)),
        run_requests.c.id.not_in(sa.select(check_runs.c.request_id).where(
            check_runs.c.request_id.is_not(None), sa.not_(legacy_run))),
        sa.cast(run_requests.c.status, sa.String).in_(_TERMINAL))).scalars())
    test_names = sorted(bind.execute(sa.select(tests.c.name).where(tests.c.id.in_(legacy_tests))).scalars())
    suite_names = sorted(set(bind.execute(
        sa.select(suites.c.name).join(suite_members, suite_members.c.suite_id == suites.c.id)
        .where(suite_members.c.test_id.in_(legacy_tests))).scalars()))

    def remove(table, where) -> int:  # noqa: ANN001
        return bind.execute(sa.delete(table).where(where)).rowcount

    counts = {
        "stage_outcomes": remove(stage_outcomes, stage_outcomes.c.run_id.in_(
            sa.select(check_runs.c.run_id).where(legacy_run))),
        "check_runs": remove(check_runs, legacy_run),
        "run_requests": remove(run_requests, run_requests.c.id.in_(emptied_requests)),
        "test_checks": remove(test_checks, sa.or_(test_checks.c.test_id.in_(legacy_tests),
                                                  test_checks.c.check_id.in_(legacy_checks))),
        "suite_members": remove(suite_members, suite_members.c.test_id.in_(legacy_tests)),
        "entity_versions": remove(entity_versions, sa.or_(
            sa.and_(entity_versions.c.entity_type == "test", entity_versions.c.entity_id.in_(legacy_tests)),
            sa.and_(entity_versions.c.entity_type == "check", entity_versions.c.entity_id.in_(legacy_checks)))),
        "tests": remove(tests, tests.c.id.in_(legacy_tests)),
        "checks": remove(checks, checks.c.id.in_(legacy_checks)),
        "worked_examples": remove(sa.table("worked_examples"), sa.true()) if has_examples else 0,
    }
    op.execute("DROP TABLE IF EXISTS worked_examples")
    if any(counts.values()):
        bind.execute(audit_events.insert().values(
            id=f"audit_{uuid.uuid4().hex[:24]}", ts=datetime.now(timezone.utc).replace(tzinfo=None),
            actor="seed", actor_kind="system", action="catalog.upgrade", entity_type="catalog",
            entity_id=None, request_source="API", before={},
            summary=(f"Removed structured tests: deleted {counts['tests']} Tests, {counts['checks']} Checks, "
                     f"{counts['check_runs']} check results and {counts['worked_examples']} worked examples."),
            after={**counts, "deleted_test_names": test_names, "affected_suite_names": suite_names}))


def downgrade() -> None:
    expectation = sa.Enum("PASS", "DENY", name="expectation").with_variant(
        postgresql.ENUM("PASS", "DENY", name="expectation", create_type=False), "postgresql")
    op.create_table(
        "worked_examples",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("intent", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("operation", sa.JSON(), nullable=True),
        sa.Column("handler_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("assertion", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expectation", expectation, nullable=False),
        sa.Column("counterfactual", sa.JSON(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("curated", sa.Boolean(), nullable=False),
        sa.Column("source_test_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_worked_examples_intent"), "worked_examples", ["intent"], unique=False)
    op.create_index(op.f("ix_worked_examples_source_test_id"), "worked_examples",
                    ["source_test_id"], unique=False)
