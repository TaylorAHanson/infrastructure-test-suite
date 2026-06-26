#!/usr/bin/env python3
"""Management CLI for the regression validation platform.

Works against whatever database the app is configured to use (SQLite locally,
Lakebase Postgres when PG* / DATABASE_URL are set). Run from anywhere:

    python backend/scripts/manage.py <command>

Commands:
    purge-runs   Delete run history only (runs, steps, requests, artifacts).
                 Keeps the test catalog, suites, targets, schedules.
    purge-seed   Delete everything that seeding creates (catalog + suites +
                 members + targets + schedules + runs). Full clean slate.
    purge-all    Alias for purge-seed (delete ALL operational rows).
    seed         Seed the catalog WITHOUT synthetic sample runs.
    seed-demo    Seed the catalog WITH synthetic sample runs (dashboard demo).
    info         Print row counts per table + the active DB URL.
"""
from __future__ import annotations

import argparse
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _counts(session) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.models import (
        Artifact, AuditEvent, RemoteAgent, RunRequest, Schedule,
        SuiteMember, TestDefinition, TestRun, TestRunStep, TestSuite, TestTarget,
    )

    models = {
        "test_definitions": TestDefinition, "test_suites": TestSuite,
        "suite_members": SuiteMember, "test_targets": TestTarget,
        "run_requests": RunRequest, "test_runs": TestRun,
        "test_run_steps": TestRunStep, "schedules": Schedule,
        "audit_events": AuditEvent, "remote_agents": RemoteAgent, "artifacts": Artifact,
    }
    out = {}
    for name, model in models.items():
        out[name] = session.execute(select(func.count()).select_from(model)).scalar() or 0
    return out


def _delete(session, models) -> None:
    """Delete all rows from the given models, in FK-safe order."""
    from sqlalchemy import delete

    for model in models:
        session.execute(delete(model))
    session.commit()


def cmd_purge_runs(session) -> None:
    from app.models import Artifact, RunRequest, TestRun, TestRunStep

    # Children first to satisfy FKs on Postgres.
    _delete(session, [Artifact, TestRunStep, TestRun, RunRequest])
    print("Deleted run history (runs, steps, requests, artifacts). Catalog kept.")


def cmd_purge_seed(session) -> None:
    from app.models import (
        Artifact, AuditEvent, RemoteAgent, RunRequest, Schedule,
        SuiteMember, TestDefinition, TestRun, TestRunStep, TestSuite, TestTarget,
    )

    _delete(session, [
        Artifact, TestRunStep, TestRun, RunRequest, SuiteMember,
        AuditEvent, Schedule, RemoteAgent, TestTarget, TestSuite, TestDefinition,
    ])
    print("Deleted ALL operational rows (clean slate).")


def cmd_seed(session, with_runs: bool) -> None:
    from app.services.seed import seed_all

    seed_all(session, with_sample_runs=with_runs)
    print(f"Seeded catalog (sample_runs={with_runs}).")


def cmd_info(session) -> None:
    from app.core.config import settings

    print(f"DB URL: {settings.sqlalchemy_url()}")
    print(f"Postgres/Lakebase: {settings.using_postgres}")
    for name, count in _counts(session).items():
        print(f"  {name:20s} {count}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Regression validation management CLI")
    parser.add_argument(
        "command",
        choices=["purge-runs", "purge-seed", "purge-all", "seed", "seed-demo", "info"],
    )
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args(argv)

    from app.db.session import create_all, session_scope

    create_all()

    destructive = args.command in ("purge-runs", "purge-seed", "purge-all")
    if destructive and not args.yes:
        resp = input(f"This will DELETE data ({args.command}). Type 'yes' to continue: ")
        if resp.strip().lower() != "yes":
            print("Aborted.")
            return 1

    with session_scope() as session:
        if args.command == "purge-runs":
            cmd_purge_runs(session)
        elif args.command in ("purge-seed", "purge-all"):
            cmd_purge_seed(session)
        elif args.command == "seed":
            cmd_seed(session, with_runs=False)
        elif args.command == "seed-demo":
            cmd_seed(session, with_runs=True)
        elif args.command == "info":
            cmd_info(session)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
