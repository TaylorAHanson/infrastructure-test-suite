"""Admin / settings endpoints: seed + purge + settings introspection + release notes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import delete, func
from sqlmodel import Session, select

from app.api.deps import actor_handle
from app.clients.databricks import get_workspace_context
from app.core.config import get_settings
from app.core.release_notes import load_release_notes
from app.core.stages import ordered_stages
from app.core.taxonomy import all_buckets
from app.db.session import get_session
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
from app.services.audit import PURGE_ALL, PURGE_RUNS, record_audit

router = APIRouter()


def _count(session: Session, model) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def _demo_mode() -> bool:
    # Same answer as databricks_available(), which builds a WorkspaceClient per
    # call (~1 s locally without a profile); the app shell fetches these settings
    # on every page load, so read the process-cached workspace context instead.
    return not (get_settings().is_databricks_app or get_workspace_context().available)


@router.get("/settings")
def get_admin_settings(session: Session = Depends(get_session)) -> dict:
    return {
        "environment": get_settings().ENVIRONMENT,
        "feature_flags": {"demo_mode": _demo_mode()},
        "canonical_stages": [{"id": s.id, "label": s.label, "connectivity": s.connectivity}
                             for s in ordered_stages()],
        "failure_taxonomy": [{"id": b.id, "connectivity": b.connectivity, "stage": b.stage}
                             for b in all_buckets()],
        "catalog_counts": {
            "checks": _count(session, Check),
            "tests": _count(session, Test),
            "suites": _count(session, Suite),
            "targets": _count(session, Target),
            "check_runs": _count(session, CheckRun),
        },
    }


@router.get("/release-notes")
def get_release_notes() -> dict:
    """The shipped ``RELEASE_NOTES.md``, newest release first, plus the running
    version so the page can mark which release is deployed."""
    return {
        "current_version": get_settings().APP_VERSION,
        "releases": [r.to_dict() for r in load_release_notes()],
    }


@router.post("/seed")
def seed_catalog(demo: bool = False, session: Session = Depends(get_session)) -> dict:
    from app.services.seed import seed_all

    seed_all(session, with_demo_targets=demo)
    return {
        "seeded": True,
        "checks": _count(session, Check),
        "tests": _count(session, Test),
        "suites": _count(session, Suite),
    }


def _purge(session: Session, models, *, actor: str, action: str, summary: str) -> dict[str, int]:
    """Bulk-delete ``models`` (children first) and audit it, in one transaction."""
    counts = {model.__name__: session.execute(delete(model)).rowcount for model in models}
    record_audit(session, actor=actor, action=action, entity_type="database",
                 summary=f"{summary}: " + ", ".join(f"{n} {name}" for name, n in counts.items()),
                 after=counts)
    return counts


@router.post("/purge-runs")
def purge_runs(session: Session = Depends(get_session),
               actor: str = Depends(actor_handle())) -> dict:
    return {"purged": True, "counts": _purge(
        session, (StageOutcome, CheckRun, RunRequest),
        actor=actor, action=PURGE_RUNS, summary="Purged run history")}


@router.post("/purge-all")
def purge_all(session: Session = Depends(get_session),
              actor: str = Depends(actor_handle())) -> dict:
    return {"purged": True, "counts": _purge(
        session,
        (StageOutcome, CheckRun, RunRequest, TestCheck, SuiteMember, Suite, Test, Check, Target),
        actor=actor, action=PURGE_ALL, summary="Purged the catalog, targets and run history")}
