"""The Assistant's tools — grounding (write-path) + RCA (read-path), ADR-0012.

The Assistant is **one agent differentiated by the tools it's given**, and the
safety guardrails **ride on the tools, not the agent** (ADR-0012 §3):

* **Write-path grounding tools** — :func:`search_sdk_surface`,
  :func:`search_targets`, :func:`search_roles`, :func:`search_examples` — feed
  the NL→Test compiler (:mod:`app.services.authoring`). ``search_roles`` returns
  **only the roles a target has actually registered** (ADR-0018, user story 24),
  so a suggested ``run-as-role`` can never name an invented or class-disallowed
  role. Grounding is over the live SDK surface + the app's own catalogs.

* **Read-path / RCA tools** — :func:`run_history`,
  :func:`commit_provenance_thread`, :func:`failure_archive`,
  :func:`operation_reference` — are **strictly read-only** (they only ``select``,
  never mutate) and their output is **advisory** (ADR-0008): it never gates and
  never changes state. This is what keeps troubleshooting from ever mutating a
  target (user story 17).
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.core import sdk_surface
from app.models import CheckRun, Target, WorkedExample
from app.services.roles import normalize_roles


# ---------------------------------------------------------------------------
# Write-path grounding tools
# ---------------------------------------------------------------------------
def search_sdk_surface(query: str, limit: int = 8) -> list[dict]:
    """Ground on the live SDK/OpenAPI surface minus the blocklist (ADR-0017)."""
    return [op.to_dict() for op in sdk_surface.search_sdk_surface(query, limit)]


def search_targets(session: Session, query: str = "", limit: int = 10) -> list[dict]:
    """Return targets matching a free-text query (name/workspace/tags)."""
    targets = session.exec(select(Target)).all()
    q = (query or "").lower().strip()

    def hit(t: Target) -> bool:
        if not q:
            return True
        hay = " ".join([t.name, t.workspace_name or "", t.host or "",
                        " ".join(f"{k}={v}" for k, v in (t.tags or {}).items())]).lower()
        return q in hay

    out = []
    for t in targets:
        if not hit(t):
            continue
        out.append({
            "id": t.id, "name": t.name, "workspace_name": t.workspace_name,
            "safety_class": t.safety_class.value,
            "roles": [r for r in (t.assumable_roles or [])],
        })
    return out[:limit]


def search_roles(session: Session, target_ref: str) -> list[dict]:
    """The assumable role-profiles a target has **registered** (ADR-0018).

    Grounding for a ``run-as-role`` suggestion: the Assistant may only offer a
    role that appears here (user story 24). Returns ``[]`` for an unknown target
    or one with no registered roles — never invents one. ``target_ref`` matches
    a target id or name.
    """
    target = _resolve_target(session, target_ref)
    if target is None:
        return []
    return [r.to_dict() for r in normalize_roles(target.assumable_roles)]


def search_examples(session: Session, query: str, limit: int = 5) -> list[dict]:
    """Rank the worked-example library against an intent (ADR-0006 §9)."""
    examples = session.exec(select(WorkedExample)).all()
    terms = [t for t in (query or "").lower().replace(",", " ").split() if len(t) > 2]

    def score(ex: WorkedExample) -> int:
        hay = " ".join([ex.intent, ex.title, ex.description, ex.assertion,
                        " ".join(ex.tags or [])]).lower()
        return sum(1 for t in terms if t in hay)

    ranked = sorted(examples, key=score, reverse=True)
    hits = [ex for ex in ranked if score(ex) > 0] or ranked[:limit]
    return [{
        "id": ex.id, "intent": ex.intent, "title": ex.title,
        "operation": ex.operation, "handler_key": ex.handler_key,
        "assertion": ex.assertion, "expectation": ex.expectation.value,
        "counterfactual": ex.counterfactual, "tags": ex.tags,
    } for ex in hits[:limit]]


def operation_reference(service: str, method: str) -> Optional[dict]:
    """Look up one SDK-surface operation (verb, stages, hints) — read-only.

    A read-path RCA tool *and* the write-path's grounding lookup: it resolves a
    ``service.method`` to its surface entry (coarse verb, canonical stages, and
    the counterfactual/evidence hints that survive of the manifest, ADR-0017 §5).
    """
    op = sdk_surface.find_operation(service, method)
    return op.to_dict() if op else None


# ---------------------------------------------------------------------------
# Read-path / RCA tools — strictly read-only, advisory
# ---------------------------------------------------------------------------
def run_history(session: Session, *, check_id: Optional[str] = None,
                target_id: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Recent CheckRuns for a (check, target), newest first — read-only."""
    stmt = select(CheckRun)
    if check_id:
        stmt = stmt.where(CheckRun.check_id == check_id)
    if target_id:
        stmt = stmt.where(CheckRun.target_id == target_id)
    stmt = stmt.order_by(CheckRun.created_at.desc())  # type: ignore[attr-defined]
    rows = session.exec(stmt).all()[:limit]
    return [_run_summary(r) for r in rows]


def commit_provenance_thread(session: Session, *, check_id: str,
                             target_id: Optional[str] = None) -> dict:
    """The last-green → first-bad thread for a (check, target) — read-only.

    *"When did this start failing?"* expressed as diff-vs-last-good (ADR-0009):
    the most recent green run and the first non-green that followed it. The
    boundary is **keyed** to the first-bad's commit ``sha`` when it is a real
    deploy, else to its non-deploy ``event_ref`` (ADR-0015) — so an Overnight
    finding with no commit still names *what changed* (see :func:`_boundary_ref`).

    The thread stays honest **even when the last green predates the retention
    window**: the sweeper preserves the most-recent green per ``(target, check)``
    as an anchor (ADR-0015), so this scan still finds it and diffs the *right*
    last-good, not the window edge.
    """
    stmt = select(CheckRun).where(CheckRun.check_id == check_id)
    if target_id:
        stmt = stmt.where(CheckRun.target_id == target_id)
    runs = sorted(session.exec(stmt).all(), key=lambda r: r.created_at)

    last_green: Optional[CheckRun] = None
    first_bad: Optional[CheckRun] = None
    for r in runs:
        if r.verdict.value == "PASS":
            last_green = r
            first_bad = None  # a later green resets the search
        elif r.verdict.value in ("FAIL", "ERROR", "BLOCKED_BY_SAFETY") and first_bad is None:
            first_bad = r
    return {
        "check_id": check_id,
        "target_id": target_id,
        "last_green": _run_summary(last_green) if last_green else None,
        "first_bad": _run_summary(first_bad) if first_bad else None,
        "boundary_ref": _boundary_ref(first_bad),
        "total_runs": len(runs),
    }


def _boundary_ref(first_bad: Optional[CheckRun]) -> Optional[dict]:
    """Key the last-green → first-bad boundary to a commit sha or an event ref.

    A deploy-attributable first-bad is keyed by its commit ``sha``; absent a real
    commit, an Overnight/non-deploy first-bad falls back to its ``event_ref``
    ("metastore grant change · 21:14"). ``None`` when neither is present.
    """
    if first_bad is None:
        return None
    if first_bad.git_sha and first_bad.git_sha != "unknown":
        return {"kind": "commit", "value": first_bad.git_sha}
    if first_bad.event_ref:
        return {"kind": "event", "value": first_bad.event_ref}
    return None


def failure_archive(session: Session, *, bucket: Optional[str] = None,
                    stage: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Prior occurrences of a ``bucket @ stage`` failure across runs — read-only."""
    stmt = select(CheckRun).where(CheckRun.verdict.in_(("FAIL", "ERROR", "BLOCKED_BY_SAFETY")))  # type: ignore[attr-defined]
    if bucket:
        stmt = stmt.where(CheckRun.failure_bucket == bucket)
    if stage:
        stmt = stmt.where(CheckRun.failure_stage == stage)
    stmt = stmt.order_by(CheckRun.created_at.desc())  # type: ignore[attr-defined]
    rows = session.exec(stmt).all()[:limit]
    return [_run_summary(r) for r in rows]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _resolve_target(session: Session, ref: str) -> Optional[Target]:
    if not ref:
        return None
    target = session.get(Target, ref)
    if target:
        return target
    return session.exec(select(Target).where(Target.name == ref)).first()


def _run_summary(r: CheckRun) -> dict:
    return {
        "run_id": r.run_id,
        "check_id": r.check_id,
        "target_id": r.target_id,
        "verdict": r.verdict.value,
        "failure_bucket": r.failure_bucket,
        "failure_stage": r.failure_stage,
        "bucket_at_stage": (f"{r.failure_bucket} @ {r.failure_stage}"
                            if r.failure_bucket and r.failure_stage else None),
        "git_sha": r.git_sha,
        "event_ref": r.event_ref,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
