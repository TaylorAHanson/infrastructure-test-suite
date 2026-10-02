"""The Assistant's tools — catalog lookups + RCA, ADR-0012.

The Assistant is **one agent differentiated by the tools it's given**, and the
safety guardrails **ride on the tools, not the agent** (ADR-0012 §3). Every
tool here is **strictly read-only** (it only reads, never mutates) and
its output is **advisory** (ADR-0008): it never gates and never changes state.
This is what keeps troubleshooting from ever mutating a target (user story 17).

* **Catalog lookups** — :func:`search_targets`, :func:`search_roles`,
  :func:`search_tests`, :func:`search_suites` — answer "do we have a test for
  X?" and name what exists. ``search_roles`` returns **only the roles a target
  has actually registered** (ADR-0018, user story 24), never an invented one.

* **RCA tools** — :func:`run_history`, :func:`commit_provenance_thread`,
  :func:`failure_archive` — the run history a triage reads.

Writing a Test is the script editor agent's job (:mod:`app.services.editor_agent`).
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import and_, func, or_
from sqlmodel import Session, select

from app.core.enums import RunStatus
from app.models import Check, CheckRun, RunRequest, Suite, SuiteMember, Target, Test, TestCheck
from app.services.roles import normalize_roles

#: Verdict values that represent a failed/bad run outcome (in-memory checks only;
#: DB-level `.in_()` calls keep their own tuples for compatibility).
_BAD_VERDICTS: frozenset[str] = frozenset({"FAIL", "ERROR", "BLOCKED_BY_SAFETY"})
_BAD_STATUSES = (RunStatus.FAIL, RunStatus.ERROR, RunStatus.BLOCKED_BY_SAFETY)

#: Exactly the columns :func:`_run_summary` / :func:`_boundary_ref` read, so the
#: read-path never loads a run's evidence blob it doesn't return.
_SUMMARY_COLUMNS = (
    CheckRun.run_id, CheckRun.check_id, CheckRun.target_id, CheckRun.verdict,
    CheckRun.failure_bucket, CheckRun.failure_stage, CheckRun.git_sha,
    CheckRun.event_ref, CheckRun.created_at,
)


def _tokenize_query(q: str) -> list[str]:
    """Normalise a query string into filter terms (len > 2, comma-tolerant)."""
    return [t for t in (q or "").lower().replace(",", " ").split() if len(t) > 2]


# ---------------------------------------------------------------------------
# Catalog lookups
# ---------------------------------------------------------------------------
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


def search_suites(session: Session, query: str = "", limit: int = 10) -> list[dict]:
    """Return available suites matching a free-text query (name/description/tags).

    Lets the model name which suites exist and how many tests each contains
    without inventing suite ids.
    """
    # One aggregating query for member counts — avoids N+1 selects per suite.
    counts: dict[str, int] = dict(session.exec(
        select(SuiteMember.suite_id, func.count(SuiteMember.id))
        .group_by(SuiteMember.suite_id)
    ).all())
    suites = session.exec(select(Suite)).all()
    q = (query or "").lower().strip()

    def hit(s: Suite) -> bool:
        if not q:
            return True
        hay = " ".join([s.name, s.description,
                        " ".join(s.tags or [])]).lower()
        return q in hay

    out = []
    for s in suites:
        if not hit(s):
            continue
        out.append({
            "id": s.id, "name": s.name, "description": s.description,
            "test_count": counts.get(s.id, 0), "deterministic_only": s.deterministic_only,
        })
    return out[:limit]


#: Per-field weight for :func:`search_tests` ranking. Each query term scores the
#: best field it appears in, so script text only breaks ties and surfaces Tests
#: that nothing more descriptive matched.
_TEST_FIELD_WEIGHTS = (("name", 4), ("tags", 3), ("description", 2), ("checks", 2), ("script", 1))


def search_tests(session: Session, query: str = "", status: Optional[str] = None,
                 limit: int = 10) -> list[dict]:
    """Search Tests (and their checks) by name, description, check names and the SDK
    calls they make, tags, script text and status, ranked by where the terms matched
    (:data:`_TEST_FIELD_WEIGHTS`).
    """
    tests = session.exec(select(Test)).all()
    # Bulk-load all TestChecks and Checks to avoid N+1 round-trips.
    all_tc = session.exec(select(TestCheck)).all()
    all_checks: dict[str, Check] = {c.id: c for c in session.exec(select(Check)).all()}
    checks_by_test: dict[str, list[Check]] = {}
    for tc in sorted(all_tc, key=lambda x: x.sort_order):
        if tc.check_id in all_checks:
            checks_by_test.setdefault(tc.test_id, []).append(all_checks[tc.check_id])

    terms = _tokenize_query(query)
    scored: list[tuple[int, dict]] = []
    for test in tests:
        if status and status != "all" and test.status.value != status:
            continue
        checks = checks_by_test.get(test.id, [])
        fields = {
            "name": test.name,
            "tags": " ".join(test.tags or []),
            "description": test.description,
            "checks": " ".join(
                f"{c.display_name} {c.description} {' '.join((c.hints or {}).get('calls') or [])}"
                for c in checks),
            "script": test.script or "",
        }
        fields = {k: v.lower() for k, v in fields.items()}
        score = sum(
            max((weight for field, weight in _TEST_FIELD_WEIGHTS if term in fields[field]), default=0)
            for term in terms)
        if terms and score == 0:
            continue
        scored.append((score, {
            "id": test.id, "name": test.name, "description": test.description,
            "tags": test.tags or [], "status": test.status.value,
            "checks": [c.display_name for c in checks], "check_count": len(checks),
        }))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [result for _, result in scored[:limit]]


# ---------------------------------------------------------------------------
# RCA tools
# ---------------------------------------------------------------------------
def run_history(session: Session, *, check_id: Optional[str] = None,
                target_id: Optional[str] = None,
                limit: int = 20) -> "list[dict] | dict":
    """Recent CheckRuns for a (check, target), newest first — read-only.

    When called with no ``check_id`` or ``target_id``, returns a global summary
    of the most recent RunRequest across all suites.
    """
    if not check_id and not target_id:
        return _global_run_summary(session)
    stmt = select(*_SUMMARY_COLUMNS)
    if check_id:
        stmt = stmt.where(CheckRun.check_id == check_id)
    if target_id:
        stmt = stmt.where(CheckRun.target_id == target_id)
    stmt = stmt.order_by(CheckRun.created_at.desc()).limit(max(limit, 0))  # type: ignore[attr-defined]
    return [_run_summary(r) for r in session.exec(stmt).all()]


def _global_run_summary(session: Session) -> dict:
    """Aggregate summary of the most recent RunRequest — no IDs required."""
    req = session.exec(select(RunRequest).order_by(RunRequest.created_at.desc())).first()  # type: ignore[attr-defined]
    if req is None:
        return {"message": "No runs have been executed yet."}
    runs = session.exec(select(*_SUMMARY_COLUMNS).where(CheckRun.request_id == req.id)).all()
    counts: dict[str, int] = {"pass": 0, "fail": 0, "error": 0, "blocked": 0, "pending": 0}
    for r in runs:
        v = r.verdict.value
        if v == "PASS":
            counts["pass"] += 1
        elif v == "FAIL":
            counts["fail"] += 1
        elif v == "ERROR":
            counts["error"] += 1
        elif v in _BAD_VERDICTS:
            counts["blocked"] += 1
        else:
            counts["pending"] += 1
    top_failures = [_run_summary(r) for r in runs if r.verdict.value in _BAD_VERDICTS][:5]
    return {
        "request_id": req.id,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "status": req.status.value,
        "git_sha": req.git_sha,
        "total": len(runs),
        **counts,
        "top_failures": top_failures,
    }


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
    scope = [CheckRun.check_id == check_id]
    if target_id:
        scope.append(CheckRun.target_id == target_id)
    last_green = session.exec(
        select(*_SUMMARY_COLUMNS).where(*scope, CheckRun.verdict == RunStatus.PASS)
        .order_by(CheckRun.created_at.desc(), CheckRun.run_id.desc()).limit(1)  # type: ignore[attr-defined]
    ).first()
    bad = select(*_SUMMARY_COLUMNS).where(*scope, CheckRun.verdict.in_(_BAD_STATUSES))  # type: ignore[attr-defined]
    if last_green is not None:
        bad = bad.where(CheckRun.created_at > last_green.created_at)
    first_bad = session.exec(
        bad.order_by(CheckRun.created_at.asc(), CheckRun.run_id.asc()).limit(1)  # type: ignore[attr-defined]
    ).first()
    total = session.exec(select(func.count()).select_from(CheckRun).where(*scope)).one()
    return _thread(check_id, target_id, last_green, first_bad, total)


def commit_provenance_threads(session: Session,
                              series: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """:func:`commit_provenance_thread` for many ``(target_id, check_id)`` series at once.

    A fixed number of statements however many series or how long their history,
    so the findings feed doesn't issue per-finding lookups. Every ``target_id``
    must be set (a target-less series means "any target" to the single-series
    tool, which this partitioning can't express).
    """
    keys = set(series)
    if not keys:
        return {}
    scope = (CheckRun.target_id.in_(sorted({t for t, _ in keys})),  # type: ignore[union-attr]
             CheckRun.check_id.in_(sorted({c for _, c in keys})))  # type: ignore[attr-defined]
    partition = (CheckRun.target_id, CheckRun.check_id)

    green_rank = func.row_number().over(
        partition_by=partition,
        order_by=(CheckRun.created_at.desc(), CheckRun.run_id.desc()),  # type: ignore[attr-defined]
    ).label("series_rank")
    greens = select(*_SUMMARY_COLUMNS, green_rank).where(
        *scope, CheckRun.verdict == RunStatus.PASS).subquery()
    last_green = {(r.target_id, r.check_id): r for r in session.exec(
        select(*(greens.c[c.key] for c in _SUMMARY_COLUMNS)).where(greens.c.series_rank == 1)
    ).all()}

    green_at = select(CheckRun.target_id, CheckRun.check_id,
                      func.max(CheckRun.created_at).label("at")).where(
        *scope, CheckRun.verdict == RunStatus.PASS).group_by(*partition).subquery()
    bad_rank = func.row_number().over(
        partition_by=partition,
        order_by=(CheckRun.created_at.asc(), CheckRun.run_id.asc()),  # type: ignore[attr-defined]
    ).label("series_rank")
    bads = (
        select(*_SUMMARY_COLUMNS, bad_rank)
        .outerjoin(green_at, and_(green_at.c.target_id == CheckRun.target_id,
                                  green_at.c.check_id == CheckRun.check_id))
        .where(*scope, CheckRun.verdict.in_(_BAD_STATUSES),  # type: ignore[attr-defined]
               or_(green_at.c.at.is_(None), CheckRun.created_at > green_at.c.at))
        .subquery()
    )
    first_bad = {(r.target_id, r.check_id): r for r in session.exec(
        select(*(bads.c[c.key] for c in _SUMMARY_COLUMNS)).where(bads.c.series_rank == 1)
    ).all()}

    totals = {(t, c): n for t, c, n in session.exec(
        select(CheckRun.target_id, CheckRun.check_id, func.count()).where(*scope).group_by(*partition)
    ).all()}

    return {key: _thread(key[1], key[0], last_green.get(key), first_bad.get(key), totals.get(key, 0))
            for key in keys}


def _thread(check_id: str, target_id: Optional[str], last_green, first_bad, total_runs: int) -> dict:
    return {
        "check_id": check_id,
        "target_id": target_id,
        "last_green": _run_summary(last_green) if last_green else None,
        "first_bad": _run_summary(first_bad) if first_bad else None,
        "boundary_ref": _boundary_ref(first_bad),
        "total_runs": total_runs,
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
    stmt = select(*_SUMMARY_COLUMNS).where(CheckRun.verdict.in_(_BAD_STATUSES))  # type: ignore[attr-defined]
    if bucket:
        stmt = stmt.where(CheckRun.failure_bucket == bucket)
    if stage:
        stmt = stmt.where(CheckRun.failure_stage == stage)
    stmt = stmt.order_by(CheckRun.created_at.desc()).limit(max(limit, 0))  # type: ignore[attr-defined]
    return [_run_summary(r) for r in session.exec(stmt).all()]


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
