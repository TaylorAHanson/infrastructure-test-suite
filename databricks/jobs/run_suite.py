"""Databricks Job entrypoint for scheduled / heavy suite execution.

This is the authoritative scheduled-execution path (the in-app poller is the
dispatcher + local-dev fallback). A Databricks Job runs this file on a cron;
each fire either:

* materialises a ``run_request`` from a ``schedule_id`` and executes it, or
* executes an existing ``request_id`` (manual heavy fan-out).

Invoked as a ``spark_python_task`` with parameters, e.g.::

    --schedule_id sched_abc            # create + run from a schedule
    --request_id  req_xyz              # run an existing request
    --execution_mode SMOKE

The job inherits the app's Lakebase + Databricks bindings via the bundle so it
reads/writes the same operational system of record. No raw credentials here.
"""
from __future__ import annotations

import argparse
import os
import sys

# Ensure the backend package is importable whether this file is run from the
# repo root (bundle sync) or the databricks/jobs directory.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKEND = os.path.join(_REPO_ROOT, "backend")
for _p in (_BACKEND, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Regression validation suite runner")
    p.add_argument("--schedule_id", default=None)
    p.add_argument("--request_id", default=None)
    p.add_argument("--execution_mode", default=None)
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    from app.db.session import create_all, session_scope
    from app.services import run_service, schedule_service

    create_all()

    request_id = args.request_id
    if request_id is None and args.schedule_id:
        with session_scope() as session:
            from app.models import Schedule

            schedule = session.get(Schedule, args.schedule_id)
            if schedule is None:
                print(f"[run_suite] schedule {args.schedule_id} not found", flush=True)
                return 2
            request = schedule_service.fire_schedule(session, schedule)
            request_id = request.id

    if request_id is None:
        print("[run_suite] nothing to do: pass --schedule_id or --request_id", flush=True)
        return 1

    print(f"[run_suite] processing request {request_id}", flush=True)
    count = run_service.process_request(request_id, worker_id="databricks-job")
    print(f"[run_suite] executed {count} run(s) for request {request_id}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
