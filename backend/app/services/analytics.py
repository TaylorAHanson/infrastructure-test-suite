"""Analytics computed from operational tables.

These power the dashboard and failure-analytics views. They are computed in
Python for portability across SQLite (local) and Lakebase/Postgres. The same
rollups are also expressed as Unity Catalog / Databricks SQL views in
``infra/sql`` for the analytical (warehouse-backed) charting path.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Optional

from sqlmodel import Session, select

from app.core.enums import (
    CONNECTIVITY_ROOT_CAUSES,
    ConnectivityOutcome,
    ProbeOutcome,
    RootCauseBucket,
    RunStatus,
)
from app.core.ids import utcnow
from app.models import SuiteMember, TestDefinition, TestRun, TestRunStep, TestTarget

_TERMINAL = {RunStatus.PASSED, RunStatus.FAILED, RunStatus.ERROR, RunStatus.PARTIAL}


def _completed_runs(session: Session, since_days: Optional[int] = None) -> list[TestRun]:
    stmt = select(TestRun)
    runs = session.exec(stmt).all()
    runs = [r for r in runs if r.status in _TERMINAL and r.finished_at]
    if since_days is not None:
        cutoff = utcnow() - timedelta(days=since_days)
        runs = [r for r in runs if r.finished_at and r.finished_at >= cutoff]
    return runs


def _is_pass(run: TestRun) -> bool:
    return run.probe_outcome == ProbeOutcome.PASS


def overview(session: Session, since_days: int = 30) -> dict:
    runs = _completed_runs(session, since_days)
    total = len(runs)
    passed = sum(1 for r in runs if _is_pass(r))
    failed = sum(1 for r in runs if r.status in (RunStatus.FAILED, RunStatus.ERROR))
    durations = [r.duration_ms for r in runs if r.duration_ms]
    connectable = sum(1 for r in runs if r.connectivity_outcome == ConnectivityOutcome.CONNECTABLE)
    return {
        "total_runs": total,
        "pass_rate": round(passed / total, 4) if total else None,
        "fail_count": failed,
        "median_duration_ms": int(statistics.median(durations)) if durations else None,
        "p95_duration_ms": int(_percentile(durations, 95)) if durations else None,
        "connectable_rate": round(connectable / total, 4) if total else None,
        "window_days": since_days,
    }


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def failure_rate_over_time(session: Session, days: int = 30) -> list[dict]:
    runs = _completed_runs(session, days)
    by_day: dict[str, list[TestRun]] = defaultdict(list)
    for r in runs:
        by_day[r.finished_at.date().isoformat()].append(r)
    out = []
    for day in sorted(by_day):
        day_runs = by_day[day]
        total = len(day_runs)
        failed = sum(1 for r in day_runs if r.status in (RunStatus.FAILED, RunStatus.ERROR))
        conn_fail = sum(
            1 for r in day_runs if r.connectivity_outcome == ConnectivityOutcome.NOT_CONNECTABLE
        )
        out.append({
            "date": day,
            "total": total,
            "failed": failed,
            "failure_rate": round(failed / total, 4) if total else 0,
            "connectivity_failures": conn_fail,
            "functional_failures": failed - conn_fail,
        })
    return out


def top_failing(session: Session, dimension: str = "test", limit: int = 10, days: int = 30) -> list[dict]:
    runs = _completed_runs(session, days)
    counts: Counter = Counter()
    totals: Counter = Counter()
    names: dict[str, str] = {}

    defs = {d.id: d for d in session.exec(select(TestDefinition)).all()}
    targets = {t.id: t for t in session.exec(select(TestTarget)).all()}

    for r in runs:
        if dimension == "target":
            key = r.target_id or "(none)"
            names[key] = targets[r.target_id].name if r.target_id in targets else "(no target)"
        else:
            key = r.test_definition_id
            names[key] = defs[key].display_name if key in defs else key
        totals[key] += 1
        if r.status in (RunStatus.FAILED, RunStatus.ERROR):
            counts[key] += 1

    rows = [
        {
            "key": k,
            "name": names.get(k, k),
            "failures": counts.get(k, 0),
            "total": totals[k],
            "failure_rate": round(counts.get(k, 0) / totals[k], 4) if totals[k] else 0,
        }
        for k in totals
    ]
    rows.sort(key=lambda x: (x["failures"], x["failure_rate"]), reverse=True)
    return rows[:limit]


def pass_rate_by(session: Session, dimension: str, days: int = 30) -> list[dict]:
    runs = _completed_runs(session, days)
    defs = {d.id: d for d in session.exec(select(TestDefinition)).all()}
    targets = {t.id: t for t in session.exec(select(TestTarget)).all()}

    groups: dict[str, list[TestRun]] = defaultdict(list)
    for r in runs:
        if dimension == "category":
            d = defs.get(r.test_definition_id)
            key = d.category.value if d else "UNKNOWN"
        elif dimension == "target":
            key = targets[r.target_id].name if r.target_id in targets else "(no target)"
        elif dimension == "suite":
            key = "(all)"  # suites resolved separately below
        else:
            key = defs[r.test_definition_id].display_name if r.test_definition_id in defs else r.test_definition_id
        groups[key].append(r)

    if dimension == "suite":
        return _pass_rate_by_suite(session, runs, defs)

    out = []
    for key, items in groups.items():
        total = len(items)
        passed = sum(1 for r in items if _is_pass(r))
        out.append({"key": key, "total": total, "pass_rate": round(passed / total, 4) if total else 0})
    out.sort(key=lambda x: x["pass_rate"])
    return out


def _pass_rate_by_suite(session: Session, runs: list[TestRun], defs: dict) -> list[dict]:
    members = session.exec(select(SuiteMember)).all()
    suite_to_defs: dict[str, set[str]] = defaultdict(set)
    for m in members:
        suite_to_defs[m.suite_id].add(m.test_definition_id)
    from app.models import TestSuite

    suites = {s.id: s for s in session.exec(select(TestSuite)).all()}
    out = []
    for suite_id, def_ids in suite_to_defs.items():
        items = [r for r in runs if r.test_definition_id in def_ids]
        total = len(items)
        passed = sum(1 for r in items if _is_pass(r))
        out.append({
            "key": suites[suite_id].name if suite_id in suites else suite_id,
            "total": total,
            "pass_rate": round(passed / total, 4) if total else 0,
        })
    out.sort(key=lambda x: x["pass_rate"])
    return out


def connectivity_vs_functional(session: Session, days: int = 30) -> dict:
    runs = _completed_runs(session, days)
    failed = [r for r in runs if r.status in (RunStatus.FAILED, RunStatus.ERROR)]
    conn = sum(1 for r in failed if r.connectivity_outcome == ConnectivityOutcome.NOT_CONNECTABLE)
    return {
        "total_failures": len(failed),
        "connectivity_failures": conn,
        "functional_failures": len(failed) - conn,
    }


def root_cause_buckets(session: Session, days: int = 30) -> list[dict]:
    runs = _completed_runs(session, days)
    counter: Counter = Counter()
    for r in runs:
        for b in (r.failure_buckets or []):
            counter[b] += 1
    return [
        {"bucket": b, "count": c, "is_connectivity": RootCauseBucket(b) in CONNECTIVITY_ROOT_CAUSES}
        for b, c in counter.most_common()
    ]


def duration_by_test(session: Session, days: int = 30) -> list[dict]:
    runs = _completed_runs(session, days)
    defs = {d.id: d for d in session.exec(select(TestDefinition)).all()}
    by_test: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        if r.duration_ms:
            by_test[r.test_definition_id].append(r.duration_ms)
    out = []
    for tid, durs in by_test.items():
        out.append({
            "test_id": tid,
            "name": defs[tid].display_name if tid in defs else tid,
            "mean_ms": int(statistics.mean(durs)),
            "p95_ms": int(_percentile(durs, 95)),
            "runs": len(durs),
        })
    out.sort(key=lambda x: x["p95_ms"], reverse=True)
    return out


def flaky_tests(session: Session, days: int = 30) -> list[dict]:
    """A test is flaky if it both passed and failed in the window."""
    runs = _completed_runs(session, days)
    defs = {d.id: d for d in session.exec(select(TestDefinition)).all()}
    by_test: dict[str, list[TestRun]] = defaultdict(list)
    for r in runs:
        by_test[r.test_definition_id].append(r)
    out = []
    for tid, items in by_test.items():
        passes = sum(1 for r in items if _is_pass(r))
        fails = sum(1 for r in items if r.status in (RunStatus.FAILED, RunStatus.ERROR))
        if passes and fails:
            total = passes + fails
            out.append({
                "test_id": tid,
                "name": defs[tid].display_name if tid in defs else tid,
                "passes": passes,
                "fails": fails,
                "flip_rate": round(min(passes, fails) / total, 4),
            })
    out.sort(key=lambda x: x["flip_rate"], reverse=True)
    return out


def last_markers(session: Session) -> list[dict]:
    runs = sorted(_completed_runs(session), key=lambda r: r.finished_at)
    defs = {d.id: d for d in session.exec(select(TestDefinition)).all()}
    last_success: dict[str, TestRun] = {}
    last_failure: dict[str, TestRun] = {}
    for r in runs:
        if _is_pass(r):
            last_success[r.test_definition_id] = r
        elif r.status in (RunStatus.FAILED, RunStatus.ERROR):
            last_failure[r.test_definition_id] = r
    out = []
    for tid in set(list(last_success) + list(last_failure)):
        out.append({
            "test_id": tid,
            "name": defs[tid].display_name if tid in defs else tid,
            "last_success": last_success[tid].finished_at.isoformat() if tid in last_success else None,
            "last_failure": last_failure[tid].finished_at.isoformat() if tid in last_failure else None,
        })
    return out


def regression_after_version(session: Session, version: str) -> list[dict]:
    """Tests that started failing on/after a given app version (deployment)."""
    runs = sorted(_completed_runs(session), key=lambda r: r.finished_at)
    defs = {d.id: d for d in session.exec(select(TestDefinition)).all()}
    before_pass: set[str] = set()
    regressed: dict[str, TestRun] = {}
    for r in runs:
        if r.app_version != version and _is_pass(r):
            before_pass.add(r.test_definition_id)
        if r.app_version == version and r.status in (RunStatus.FAILED, RunStatus.ERROR):
            if r.test_definition_id in before_pass:
                regressed[r.test_definition_id] = r
    return [
        {
            "test_id": tid,
            "name": defs[tid].display_name if tid in defs else tid,
            "version": version,
            "root_cause": run.root_cause_bucket.value if run.root_cause_bucket else None,
        }
        for tid, run in regressed.items()
    ]


def privatelink_failures(session: Session, days: int = 30) -> dict:
    """PrivateLink-specific failure views, grouped from step root causes."""
    cutoff = utcnow() - timedelta(days=days)
    steps = session.exec(select(TestRunStep)).all()
    buckets = {
        "dns_failures": {RootCauseBucket.DNS_RESOLUTION_FAILURE, RootCauseBucket.PRIVATE_DNS_MISCONFIGURATION},
        "auth_failures": {RootCauseBucket.OAUTH_SCOPE_MISSING, RootCauseBucket.PERMISSION_DENIED},
        "outbound_deny_failures": {RootCauseBucket.NETWORK_POLICY_DENY, RootCauseBucket.NCC_NOT_ATTACHED},
        "handshake_failures": {RootCauseBucket.TLS_HANDSHAKE_FAILURE, RootCauseBucket.PRIVATELINK_ENDPOINT_NOT_ESTABLISHED},
        "read_write_failures": {RootCauseBucket.DATA_VALIDATION_FAILURE},
    }
    result = {k: 0 for k in buckets}
    for s in steps:
        if not s.root_cause_bucket or not s.finished_at or s.finished_at < cutoff:
            continue
        for view, rcs in buckets.items():
            if s.root_cause_bucket in rcs:
                result[view] += 1
    return result
