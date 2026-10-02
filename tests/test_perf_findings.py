"""The findings feed and the RCA read tools stay bounded as history grows.

The feed is polled every 15 s over Retained history, so its SQL work must not
scale with how many runs a series has: series heads, provenance threads and
flaky flags are computed in a fixed number of statements, and the Assistant's
history tools put their limit in SQL and never load evidence. The randomized
case pins the output to the original whole-history algorithm.
"""
from __future__ import annotations

import random
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import event

from app.core.enums import Evaluator, RequestSource, RunStatus
from app.core.ids import utcnow
from app.db.session import get_engine
from app.models import Check, CheckRun, Target
from app.services import assistant_tools, findings

_BAD = {RunStatus.FAIL, RunStatus.ERROR, RunStatus.BLOCKED_BY_SAFETY}
_TERMINAL = _BAD | {RunStatus.PASS, RunStatus.SKIPPED, RunStatus.CANCELLED}


@contextmanager
def _statements():
    seen: list[str] = []

    def _capture(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        seen.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _capture)


def _check_and_target(session, name: str) -> tuple[Check, Target]:
    check = Check(display_name=name, assertion="x")
    target = Target(name=f"{name}-tgt")
    session.add(check)
    session.add(target)
    session.commit()
    session.refresh(check)
    session.refresh(target)
    return check, target


def _history(session, check_id, target_id, verdicts, *, start_minutes_ago: int) -> None:
    for i, verdict in enumerate(verdicts):
        ts = utcnow() - timedelta(minutes=start_minutes_ago - i)
        session.add(CheckRun(check_id=check_id, target_id=target_id, verdict=verdict,
                             git_sha=f"sha{i}", created_at=ts, finished_at=ts,
                             evidence={"blob": "x" * 200}))
    session.commit()


def _feed_statement_count(session, *, series: int, runs_per_series: int) -> int:
    for n in range(series):
        check, target = _check_and_target(session, f"s{runs_per_series}-{n}")
        verdicts = [RunStatus.PASS, RunStatus.FAIL] * (runs_per_series // 2)
        _history(session, check.id, target.id, verdicts + [RunStatus.FAIL],
                 start_minutes_ago=runs_per_series + 10)
    with _statements() as seen:
        feed = findings.build_feed(session)
    assert sum(len(l["findings"]) for l in feed["lanes"]) == series
    return len(seen)


def test_feed_statement_count_does_not_grow_with_history_length(session):
    short = _feed_statement_count(session, series=3, runs_per_series=4)
    from app.db.session import create_all  # fresh tables for the long-history run
    from sqlmodel import SQLModel

    SQLModel.metadata.drop_all(get_engine())
    create_all()
    long = _feed_statement_count(session, series=3, runs_per_series=120)
    assert long == short


def test_feed_statement_count_does_not_grow_with_finding_count(session):
    few = _feed_statement_count(session, series=2, runs_per_series=6)
    from app.db.session import create_all
    from sqlmodel import SQLModel

    SQLModel.metadata.drop_all(get_engine())
    create_all()
    many = _feed_statement_count(session, series=25, runs_per_series=6)
    assert many == few


def test_feed_never_selects_evidence(session):
    check, target = _check_and_target(session, "evidence-heavy")
    _history(session, check.id, target.id, [RunStatus.PASS, RunStatus.FAIL], start_minutes_ago=5)
    with _statements() as seen:
        findings.build_feed(session)
    assert not any("evidence" in s for s in seen)


# ------------------------- identical to the whole-history scan -------------------------
def _old_heads(rows):
    latest = {}
    for r in sorted(rows, key=lambda r: r.created_at, reverse=True):
        if r.verdict in _TERMINAL:
            latest.setdefault((r.target_id, r.check_id), r)
    return latest


def _old_thread(rows, check_id, target_id):
    scoped = sorted((r for r in rows if r.check_id == check_id
                     and (not target_id or r.target_id == target_id)), key=lambda r: r.created_at)
    last_green = first_bad = None
    for r in scoped:
        if r.verdict == RunStatus.PASS:
            last_green, first_bad = r, None
        elif r.verdict in _BAD and first_bad is None:
            first_bad = r
    return (last_green.run_id if last_green else None,
            first_bad.run_id if first_bad else None, len(scoped))


def _old_flaky(rows, check_id, target_id, window=10):
    scoped = sorted((r for r in rows if r.check_id == check_id
                     and r.evaluator == Evaluator.DETERMINISTIC
                     and (target_id is None or r.target_id == target_id)
                     and (r.verdict == RunStatus.PASS or r.verdict in _BAD)),
                    key=lambda r: r.created_at, reverse=True)[:window]
    greens = [r.verdict == RunStatus.PASS for r in scoped]
    return sum(1 for a, b in zip(greens, greens[1:]) if a != b) >= 2


def test_feed_matches_the_whole_history_algorithm_on_random_histories(session):
    rng = random.Random(1234)
    verdict_pool = [RunStatus.PASS] * 4 + [RunStatus.FAIL] * 3 + [
        RunStatus.ERROR, RunStatus.BLOCKED_BY_SAFETY, RunStatus.SKIPPED,
        RunStatus.CANCELLED, RunStatus.PENDING, RunStatus.RUNNING]
    checks = []
    targets = []
    for n in range(6):
        c, t = _check_and_target(session, f"rand-{n}")
        checks.append(c)
        targets.append(t)
    minute = 0
    for check in checks:
        # Several targets per check, plus a target-less series ("any target").
        for target_id in [t.id for t in rng.sample(targets, 3)] + [None]:
            for _ in range(rng.randint(0, 25)):
                minute += 1
                ts = utcnow() - timedelta(minutes=10_000 - minute)
                session.add(CheckRun(
                    check_id=check.id, target_id=target_id, verdict=rng.choice(verdict_pool),
                    evaluator=rng.choice([Evaluator.DETERMINISTIC] * 4 + [Evaluator.AGENTIC]),
                    request_source=rng.choice([RequestSource.CI, RequestSource.NIGHTLY]),
                    git_sha=rng.choice(["unknown", f"sha{minute}"]),
                    event_ref=rng.choice([None, f"event {minute}"]),
                    created_at=ts, finished_at=ts))
    session.commit()

    from sqlmodel import select

    rows = session.exec(select(CheckRun)).all()
    heads = _old_heads(rows)
    feed = findings.build_feed(session, limit=1000)
    got = {(f["target_id"], f["check_id"]): f for l in feed["lanes"] for f in l["findings"]}

    expected_findings = {k: r for k, r in heads.items() if r.verdict in _BAD}
    assert set(got) == set(expected_findings)
    assert sum(l["everything_else"] for l in feed["lanes"]) == len(heads) - len(expected_findings)
    for key, head in expected_findings.items():
        finding = got[key]
        assert finding["run_id"] == head.run_id
        thread = finding["provenance"]
        assert (
            thread["last_green"]["run_id"] if thread["last_green"] else None,
            thread["first_bad"]["run_id"] if thread["first_bad"] else None,
            thread["total_runs"],
        ) == _old_thread(rows, head.check_id, head.target_id), key
        assert finding["flaky"] == _old_flaky(rows, head.check_id, head.target_id), key
        single = assistant_tools.commit_provenance_thread(
            session, check_id=head.check_id, target_id=head.target_id)
        assert single == thread


# ----------------------------- RCA read tools: limit in SQL -----------------------------
def test_run_history_limits_in_sql_and_skips_evidence(session):
    check, target = _check_and_target(session, "history")
    check_id, target_id = check.id, target.id
    _history(session, check_id, target_id, [RunStatus.FAIL] * 30, start_minutes_ago=40)
    with _statements() as seen:
        rows = assistant_tools.run_history(session, check_id=check_id, target_id=target_id, limit=5)
    assert len(rows) == 5
    assert rows[0]["created_at"] > rows[-1]["created_at"]
    assert seen and all("LIMIT" in s.upper() and "evidence" not in s for s in seen), seen


def test_failure_archive_limits_in_sql_and_skips_evidence(session):
    check, target = _check_and_target(session, "archive")
    _history(session, check.id, target.id, [RunStatus.FAIL, RunStatus.PASS] * 15, start_minutes_ago=40)
    with _statements() as seen:
        rows = assistant_tools.failure_archive(session, limit=4)
    assert len(rows) == 4
    assert {r["verdict"] for r in rows} == {"FAIL"}
    assert seen and all("LIMIT" in s.upper() and "evidence" not in s for s in seen), seen
