"""Delete what the structured-test feature left behind.

Tests used to come in two kinds: structured Tests (no script; Checks carrying an
operation + assertion for a handler to run) and script Tests. Only script Tests
remain, so an install that predates that holds rows nothing can run, edit or
show any more. :func:`purge_legacy` deletes them, with everything that points at
them, in one transaction, and records one audit event saying what it removed:

* every Test without a script, and every Check whose handler isn't ``script``
  (plus a script Check left owned by a deleted Test that no other Test lists);
* their Test and Suite memberships and their version snapshots;
* every CheckRun of those Checks or Tests and its StageOutcomes, and the
  RunRequest of a finished run when every result it held was one of those;
* the worked-example library (the ``worked_examples`` table, dropped).

Suites are kept, even when they lose every member. On a current install there's
nothing to find, so it reads a few rows and writes nothing — it runs on every
seed. Migration ``wf43legacypurge`` performs the same deletes on Postgres.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlmodel import Session, col, select

from app.core.enums import TERMINAL_STATUSES
from app.models import Check, CheckRun, EntityVersion, RunRequest, StageOutcome, Suite, SuiteMember, Test, TestCheck
from app.services.audit import CATALOG_UPGRADE, record_audit

logger = logging.getLogger(__name__)

SCRIPT_HANDLER = "script"
WORKED_EXAMPLES_TABLE = "worked_examples"
_COUNTED = ("stage_outcomes", "check_runs", "run_requests", "test_checks", "suite_members",
            "entity_versions", "tests", "checks", "worked_examples")


def _legacy_test_ids(session: Session) -> set[str]:
    return set(session.exec(select(Test.id).where(sa.or_(col(Test.script).is_(None), Test.script == ""))).all())


def _legacy_check_ids(session: Session, tests: set[str]) -> set[str]:
    ids = set(session.exec(select(Check.id).where(Check.handler_key != SCRIPT_HANDLER)).all())
    listed = set(session.exec(select(TestCheck.check_id).where(col(TestCheck.test_id).not_in(tests))).all())
    for check_id, hints in session.exec(
            select(Check.id, Check.hints).where(Check.handler_key == SCRIPT_HANDLER)).all():
        if (hints or {}).get("script_test_id") in tests and check_id not in listed:
            ids.add(check_id)
    return ids


def _has_worked_examples(session: Session) -> bool:
    return sa.inspect(session.connection()).has_table(WORKED_EXAMPLES_TABLE)


def _drop_worked_examples(session: Session) -> int:
    if not _has_worked_examples(session):
        return 0
    connection = session.connection()
    deleted = connection.execute(sa.delete(sa.table(WORKED_EXAMPLES_TABLE))).rowcount
    connection.execute(sa.text(f"DROP TABLE {WORKED_EXAMPLES_TABLE}"))
    return deleted


def purge_legacy(session: Session, *, actor: str) -> dict[str, int]:
    """Delete every structured Test and non-script Check and everything hanging off them.

    Returns the number of rows deleted per table, all zero when there was nothing
    to delete. On any error the transaction is rolled back and nothing is deleted.
    """
    def remove(model, where) -> int:  # noqa: ANN001
        return session.execute(sa.delete(model).where(where)
                               .execution_options(synchronize_session=False)).rowcount

    tests = _legacy_test_ids(session)
    checks = _legacy_check_ids(session, tests)
    if not tests and not checks and not _has_worked_examples(session):
        return dict.fromkeys(_COUNTED, 0)
    try:
        legacy_run = sa.or_(col(CheckRun.check_id).in_(checks),
                            sa.and_(col(CheckRun.test_id).is_not(None), col(CheckRun.test_id).in_(tests)))
        kept_requests = select(CheckRun.request_id).where(col(CheckRun.request_id).is_not(None),
                                                          sa.not_(legacy_run))
        emptied_requests = set(session.exec(select(RunRequest.id).where(
            col(RunRequest.id).in_(select(CheckRun.request_id).where(legacy_run)),
            col(RunRequest.id).not_in(kept_requests),
            col(RunRequest.status).in_(list(TERMINAL_STATUSES)))).all())
        test_names = sorted(session.exec(select(Test.name).where(col(Test.id).in_(tests))).all())
        suite_names = sorted(set(session.exec(
            select(Suite.name).join(SuiteMember, col(SuiteMember.suite_id) == Suite.id)
            .where(col(SuiteMember.test_id).in_(tests))).all()))

        counts = {
            "stage_outcomes": remove(StageOutcome, col(StageOutcome.run_id).in_(
                select(CheckRun.run_id).where(legacy_run))),
            "check_runs": remove(CheckRun, legacy_run),
            "run_requests": remove(RunRequest, col(RunRequest.id).in_(emptied_requests)),
            "test_checks": remove(TestCheck, sa.or_(col(TestCheck.test_id).in_(tests),
                                                    col(TestCheck.check_id).in_(checks))),
            "suite_members": remove(SuiteMember, col(SuiteMember.test_id).in_(tests)),
            "entity_versions": remove(EntityVersion, sa.or_(
                sa.and_(EntityVersion.entity_type == "test", col(EntityVersion.entity_id).in_(tests)),
                sa.and_(EntityVersion.entity_type == "check", col(EntityVersion.entity_id).in_(checks)))),
            "tests": remove(Test, col(Test.id).in_(tests)),
            "checks": remove(Check, col(Check.id).in_(checks)),
            "worked_examples": _drop_worked_examples(session),
        }
        if any(counts.values()):
            record_audit(
                session, actor=actor, actor_kind="system", action=CATALOG_UPGRADE, entity_type="catalog",
                summary=(f"Removed structured tests: deleted {counts['tests']} Tests, {counts['checks']} Checks, "
                         f"{counts['check_runs']} check results and {counts['worked_examples']} worked examples."),
                after={**counts, "deleted_test_names": test_names, "affected_suite_names": suite_names},
                commit=False)
        session.commit()
    except Exception:
        session.rollback()
        raise
    if any(counts.values()):
        logger.info("Removed structured tests and their history: %s", counts)
    return counts
