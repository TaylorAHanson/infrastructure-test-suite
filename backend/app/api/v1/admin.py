"""Admin / settings endpoints: seed + purge + registry introspection."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.core.stages import ordered_stages
from app.core.taxonomy import all_buckets
from app.db.session import get_session
from app.executors.registry import registry
from app.models import (
    Check,
    CheckRun,
    RunRequest,
    StageOutcome,
    Suite,
    SuiteMember,
    Target,
    Test,
    TestCheck,
)

router = APIRouter()


@router.get("/settings")
def get_admin_settings(session: Session = Depends(get_session)) -> dict:
    return {
        "registered_handler_keys": sorted(registry.keys()),
        "canonical_stages": [{"id": s.id, "label": s.label, "connectivity": s.connectivity}
                             for s in ordered_stages()],
        "failure_taxonomy": [{"id": b.id, "connectivity": b.connectivity, "stage": b.stage}
                             for b in all_buckets()],
        "catalog_counts": {
            "checks": len(session.exec(select(Check)).all()),
            "tests": len(session.exec(select(Test)).all()),
            "suites": len(session.exec(select(Suite)).all()),
            "targets": len(session.exec(select(Target)).all()),
            "check_runs": len(session.exec(select(CheckRun)).all()),
        },
    }


@router.post("/seed")
def seed_catalog(demo: bool = False, session: Session = Depends(get_session)) -> dict:
    from app.services.seed import seed_all

    seed_all(session, with_demo_targets=demo)
    return {
        "seeded": True,
        "checks": len(session.exec(select(Check)).all()),
        "tests": len(session.exec(select(Test)).all()),
        "suites": len(session.exec(select(Suite)).all()),
    }


def _purge(session: Session, models) -> dict[str, int]:
    counts: dict[str, int] = {}
    for model in models:
        rows = session.exec(select(model)).all()
        counts[model.__name__] = len(rows)
        for row in rows:
            session.delete(row)
        session.flush()
    session.commit()
    return counts


@router.post("/purge-runs")
def purge_runs(session: Session = Depends(get_session)) -> dict:
    return {"purged": True, "counts": _purge(session, (StageOutcome, CheckRun, RunRequest))}


@router.post("/purge-all")
def purge_all(session: Session = Depends(get_session)) -> dict:
    return {"purged": True, "counts": _purge(
        session,
        (StageOutcome, CheckRun, RunRequest, TestCheck, SuiteMember, Suite, Test, Check, Target),
    )}
