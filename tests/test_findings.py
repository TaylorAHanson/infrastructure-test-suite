"""Findings feed backend — two lanes + Everything-else (#33, ADR-0009).

The feed turns retained run history into a ranked list of **findings, not raw
runs**: one finding per ``(target, check)`` series whose *current* state is
actionable. Non-actionable series (nominal / recovered) never surface as
findings — they collapse into a single **"Everything else"** count per lane.
Each finding is classified into **Recent-deployments** (CI-triggered,
commit-scoped, within the 24h window) or **Overnight** (the default when a
result isn't attributable to a deploy), and states its failure as
``bucket @ stage``.

Primary seam is the HTTP API over a seeded green→bad→recover history; a few
service-seam cases pin the lane/everything-else edges directly.
"""
from __future__ import annotations

from datetime import timedelta

from app.core.enums import Evaluator, RequestSource, RunStatus
from app.core.ids import utcnow
from app.db.session import session_scope
from app.models import Check, CheckRun, Target
from app.services import findings
from app.services.rca import LANE_OVERNIGHT, LANE_RECENT


# --------------------------------------------------------------------------
# seeding helpers — one (check, target) pair per series
# --------------------------------------------------------------------------
def _series(session, check_name: str, specs: list[dict]) -> tuple[Check, Target]:
    """Seed a fresh (check, target) and a run per spec, oldest first."""
    check = Check(display_name=check_name, assertion="the operation succeeds")
    target = Target(name=f"{check_name}-tgt", workspace_name=f"{check_name}-ws")
    session.add(check)
    session.add(target)
    session.commit()
    session.refresh(check)
    session.refresh(target)
    n = len(specs)
    for i, spec in enumerate(specs):
        spec = {"minutes_ago": (n - i) * 10, **spec}
        _run(session, check, target, **spec)
    return check, target


def _run(session, check, target, verdict, *, source=RequestSource.NIGHTLY,
         git_sha="unknown", bucket=None, stage=None, minutes_ago=0,
         evaluator=Evaluator.DETERMINISTIC, gating=True) -> CheckRun:
    ts = utcnow() - timedelta(minutes=minutes_ago)
    run = CheckRun(
        check_id=check.id, target_id=target.id, verdict=verdict,
        request_source=source, git_sha=git_sha,
        failure_bucket=bucket, failure_stage=stage,
        evaluator=evaluator, gating=gating,
        started_at=ts, finished_at=ts, created_at=ts,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _feed(client, **params) -> dict:
    resp = client.get("/api/v1/findings", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _lane(feed: dict, lane: str) -> dict:
    return next(l for l in feed["lanes"] if l["lane"] == lane)


_FAIL = {"verdict": RunStatus.FAIL, "bucket": "OPERATION_FAILED", "stage": "OPERATE"}


# --------------------------------------------------------------------------
# HTTP seam — the acceptance criteria
# --------------------------------------------------------------------------
def test_endpoint_returns_two_lanes(client):
    feed = _feed(client)
    assert {l["lane"] for l in feed["lanes"]} == {LANE_RECENT, LANE_OVERNIGHT}
    for lane in feed["lanes"]:
        assert lane["findings"] == []
        assert lane["everything_else"] == 0


def test_green_bad_recover_history_lane_and_everything_else(client):
    # A stays-bad series is a finding; a recovered series (green→bad→recover)
    # and an always-green series are non-actionable and collapse into
    # "Everything else" — never findings.
    with session_scope() as s:
        _series(s, "stays broken", [
            {"verdict": RunStatus.PASS, "git_sha": "g1"},
            _FAIL,
        ])
        _series(s, "recovered", [
            {"verdict": RunStatus.PASS},
            _FAIL,
            {"verdict": RunStatus.PASS},
        ])
        _series(s, "always green", [{"verdict": RunStatus.PASS}])

    overnight = _lane(_feed(client), LANE_OVERNIGHT)
    assert [f["check_name"] for f in overnight["findings"]] == ["stays broken"]
    assert overnight["everything_else"] == 2             # recovered + nominal


def test_deploy_attributed_lands_in_recent_unattributed_defaults_overnight(client):
    with session_scope() as s:
        _series(s, "ci regression", [
            {"verdict": RunStatus.PASS, "source": RequestSource.CI, "git_sha": "abc123"},
            {"verdict": RunStatus.FAIL, "source": RequestSource.CI, "git_sha": "def456",
             "bucket": "DATA_VALIDATION_FAILURE", "stage": "VERIFY"},
        ])
        _series(s, "overnight drift", [
            {"verdict": RunStatus.FAIL, "source": RequestSource.NIGHTLY,
             "bucket": "PERMISSION_DENIED", "stage": "AUTHORIZE"},
        ])

    feed = _feed(client)
    assert [f["check_name"] for f in _lane(feed, LANE_RECENT)["findings"]] == ["ci regression"]
    assert [f["check_name"] for f in _lane(feed, LANE_OVERNIGHT)["findings"]] == ["overnight drift"]


def test_ci_without_commit_or_outside_window_defaults_overnight(client):
    with session_scope() as s:
        # CI-triggered but no commit sha → ambiguous → Overnight default.
        _series(s, "ci no commit", [
            {"verdict": RunStatus.FAIL, "source": RequestSource.CI, "git_sha": "unknown",
             "bucket": "OPERATION_FAILED", "stage": "OPERATE"},
        ])
        # CI + commit but older than the 24h window → not "recent".
        _series(s, "ci stale", [
            {"verdict": RunStatus.FAIL, "source": RequestSource.CI, "git_sha": "old999",
             "bucket": "OPERATION_FAILED", "stage": "OPERATE", "minutes_ago": 60 * 48},
        ])

    feed = _feed(client)
    assert _lane(feed, LANE_RECENT)["findings"] == []
    assert {f["check_name"] for f in _lane(feed, LANE_OVERNIGHT)["findings"]} == {"ci no commit", "ci stale"}


def test_finding_exposes_bucket_at_stage(client):
    with session_scope() as s:
        _series(s, "bucketed", [
            {"verdict": RunStatus.FAIL, "bucket": "PERMISSION_DENIED", "stage": "AUTHORIZE"},
        ])

    finding = _lane(_feed(client), LANE_OVERNIGHT)["findings"][0]
    assert finding["bucket_at_stage"] == "PERMISSION_DENIED @ AUTHORIZE"
    assert finding["failure_bucket"] == "PERMISSION_DENIED"
    assert finding["failure_stage"] == "AUTHORIZE"


def test_repeated_failures_collapse_to_one_finding(client):
    # "Findings, not raw runs": a series failing three runs running is ONE
    # finding (its current state), not three.
    with session_scope() as s:
        _series(s, "flapping bad", [_FAIL, _FAIL, _FAIL])

    assert len(_lane(_feed(client), LANE_OVERNIGHT)["findings"]) == 1


def test_findings_ranked_newest_first(client):
    with session_scope() as s:
        _series(s, "older break", [{**_FAIL, "minutes_ago": 120}])
        _series(s, "newer break", [{**_FAIL, "minutes_ago": 5}])

    names = [f["check_name"] for f in _lane(_feed(client), LANE_OVERNIGHT)["findings"]]
    assert names == ["newer break", "older break"]


def test_blocked_by_safety_is_actionable(client):
    with session_scope() as s:
        _series(s, "blocked op", [
            {"verdict": RunStatus.BLOCKED_BY_SAFETY, "bucket": "BLOCKED_BY_SAFETY",
             "stage": "AUTHORIZE"},
        ])

    findings_list = _lane(_feed(client), LANE_OVERNIGHT)["findings"]
    assert [f["verdict"] for f in findings_list] == ["BLOCKED_BY_SAFETY"]


# --------------------------------------------------------------------------
# service seam — the classification edges, unit-tested directly
# --------------------------------------------------------------------------
def test_service_partitions_each_series_exactly_once(session):
    _series(session, "a-broken", [_FAIL])
    _series(session, "b-green", [{"verdict": RunStatus.PASS}])

    feed = findings.build_feed(session)
    assert sum(len(l["findings"]) for l in feed["lanes"]) == 1
    assert sum(l["everything_else"] for l in feed["lanes"]) == 1


def test_service_window_hours_override(session):
    _series(session, "windowed", [
        {"verdict": RunStatus.FAIL, "source": RequestSource.CI, "git_sha": "sha",
         "bucket": "OPERATION_FAILED", "stage": "OPERATE", "minutes_ago": 60 * 5},
    ])

    # Default 24h window → recent. A 1h window pushes the 5h-old run to overnight.
    default = findings.build_feed(session)
    assert len(_lane(default, LANE_RECENT)["findings"]) == 1
    narrowed = findings.build_feed(session, window_hours=1)
    assert _lane(narrowed, LANE_RECENT)["findings"] == []
    assert len(_lane(narrowed, LANE_OVERNIGHT)["findings"]) == 1


def test_service_ignores_non_terminal_runs(session):
    check = Check(display_name="pending", assertion="x")
    target = Target(name="pending-tgt")
    session.add(check)
    session.add(target)
    session.commit()
    session.refresh(check)
    session.refresh(target)
    _run(session, check, target, RunStatus.PENDING)
    _run(session, check, target, RunStatus.RUNNING, minutes_ago=1)

    feed = findings.build_feed(session)
    assert all(l["findings"] == [] and l["everything_else"] == 0 for l in feed["lanes"])
