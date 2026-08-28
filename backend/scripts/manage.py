#!/usr/bin/env python3
"""Management CLI for the validation platform.

Works against whatever database the app is configured to use (SQLite locally,
Lakebase Postgres when PG* / DATABASE_URL are set). Run from anywhere:

    python backend/scripts/manage.py <command>

Commands:
    purge-runs   Delete run history only (check_runs, stage_outcomes, requests).
                 Keeps the Check/Test/Suite catalog + targets.
    purge-seed   Delete everything (catalog + tests + suites + targets + runs).
    purge-all    Alias for purge-seed (delete ALL operational rows).
    seed         Seed the Check/Test/Suite catalog (no demo targets).
    seed-demo    Seed the catalog WITH demo targets.
    info         Print row counts per table + the active DB URL.
"""
from __future__ import annotations

import argparse
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _models():
    from app.models import (
        AuditEvent, Check, CheckRun, EntityVersion, RunRequest,
        StageOutcome, Suite, SuiteMember, Target, Test, TestCheck,
    )

    return {
        "checks": Check, "tests": Test, "test_checks": TestCheck,
        "suites": Suite, "suite_members": SuiteMember, "targets": Target,
        "entity_versions": EntityVersion, "run_requests": RunRequest,
        "check_runs": CheckRun, "stage_outcomes": StageOutcome, "audit_events": AuditEvent,
    }


def _counts(session) -> dict[str, int]:
    from sqlalchemy import func, select

    out = {}
    for name, model in _models().items():
        out[name] = session.execute(select(func.count()).select_from(model)).scalar() or 0
    return out


def _delete(session, models) -> None:
    from sqlalchemy import delete

    for model in models:
        session.execute(delete(model))
    session.commit()


def cmd_purge_runs(session) -> None:
    from app.models import CheckRun, RunRequest, StageOutcome

    _delete(session, [StageOutcome, CheckRun, RunRequest])
    print("Deleted run history (stage_outcomes, check_runs, requests). Catalog kept.")


def cmd_purge_seed(session) -> None:
    from app.models import (
        AuditEvent, Check, CheckRun, EntityVersion, RunRequest,
        StageOutcome, Suite, SuiteMember, Target, Test, TestCheck,
    )

    _delete(session, [
        StageOutcome, CheckRun, RunRequest, TestCheck, SuiteMember,
        AuditEvent, EntityVersion, Suite, Test, Check, Target,
    ])
    print("Deleted ALL operational rows (clean slate).")


def cmd_seed(session, with_demo: bool) -> None:
    from app.services.seed import seed_all

    seed_all(session, with_demo_targets=with_demo)
    print(f"Seeded catalog (demo_targets={with_demo}).")


def cmd_info(session) -> None:
    from app.core.config import settings

    print(f"DB URL: {settings.sqlalchemy_url()}")
    print(f"Postgres/Lakebase: {settings.using_postgres}")
    for name, count in _counts(session).items():
        print(f"  {name:20s} {count}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validation platform management CLI")
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
            cmd_seed(session, with_demo=False)
        elif args.command == "seed-demo":
            cmd_seed(session, with_demo=True)
        elif args.command == "info":
            cmd_info(session)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
