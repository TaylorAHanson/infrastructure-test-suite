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

from sqlmodel import select

from app.core.enums import Evaluator, RequestSource, RunStatus
from app.core.ids import utcnow
from app.db.session import session_scope
from app.models import Check, CheckRun, Suite, SuiteMember, Target, Test as CatalogTest, TestCheck
from app.services import findings, retention
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
         event_ref=None, evaluator=Evaluator.DETERMINISTIC, gating=True) -> CheckRun:
    ts = utcnow() - timedelta(minutes=minutes_ago)
    run = CheckRun(
        check_id=check.id, target_id=target.id, verdict=verdict,
        request_source=source, git_sha=git_sha, event_ref=event_ref,
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


def test_finding_names_the_test_and_suites_so_a_triager_can_open_them(client):
    # A finding is a Check result; the triager needs the Test it belongs to
    # (to edit the assertion / limit) and the Suite it ran in.
    with session_scope() as s:
        check, target = _series(s, "few service principals are workspace admins", [_FAIL])
        test = CatalogTest(name="Admin footprint")
        suite = Suite(name="Security posture (drift)")
        s.add(test)
        s.add(suite)
        s.commit()
        s.refresh(test)
        s.refresh(suite)
        s.add(TestCheck(test_id=test.id, check_id=check.id, sort_order=0))
        s.add(SuiteMember(suite_id=suite.id, test_id=test.id, sort_order=0))
        s.commit()
        test_id, suite_id = test.id, suite.id

    finding = _lane(_feed(client), LANE_OVERNIGHT)["findings"][0]
    assert finding["tests"] == [{"id": test_id, "name": "Admin footprint"}]
    assert finding["suites"] == [{"id": suite_id, "name": "Security posture (drift)"}]


def test_finding_prefers_the_test_that_ran_when_a_check_is_shared(client):
    with session_scope() as s:
        check, target = _series(s, "shared check", [_FAIL])
        ran = CatalogTest(name="The test that ran")
        other = CatalogTest(name="Another test sharing the check")
        s.add(ran)
        s.add(other)
        s.commit()
        s.refresh(ran)
        s.refresh(other)
        s.add(TestCheck(test_id=ran.id, check_id=check.id, sort_order=0))
        s.add(TestCheck(test_id=other.id, check_id=check.id, sort_order=0))
        run = s.exec(select(CheckRun).where(CheckRun.check_id == check.id)).one()
        run.test_id = ran.id
        s.add(run)
        s.commit()
        ran_id = ran.id

    finding = _lane(_feed(client), LANE_OVERNIGHT)["findings"][0]
    assert finding["tests"] == [{"id": ran_id, "name": "The test that ran"}]


def test_finding_exposes_bucket_at_stage(client):
    with session_scope() as s:
        _series(s, "bucketed", [
            {"verdict": RunStatus.FAIL, "bucket": "PERMISSION_DENIED", "stage": "AUTHORIZE"},
        ])

    finding = _lane(_feed(client), LANE_OVERNIGHT)["findings"][0]
    assert finding["bucket_at_stage"] == "PERMISSION_DENIED @ AUTHORIZE"
    assert finding["failure_label"] == "Permission denied"
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


# --------------------------------------------------------------------------
# #34 — commit-provenance thread + flaky advisory (ADR-0009 / ADR-0008)
# --------------------------------------------------------------------------
def test_finding_carries_last_green_to_first_bad_keyed_to_commit(session):
    # A CI regression's finding threads last-green → first-bad, keyed to the
    # first-bad commit sha.
    _series(session, "ci regression", [
        {"verdict": RunStatus.PASS, "source": RequestSource.CI, "git_sha": "green1"},
        {"verdict": RunStatus.FAIL, "source": RequestSource.CI, "git_sha": "bad2",
         "bucket": "OPERATION_FAILED", "stage": "OPERATE"},
    ])

    finding = _lane(findings.build_feed(session), LANE_RECENT)["findings"][0]
    thread = finding["provenance"]
    assert thread["last_green"]["git_sha"] == "green1"
    assert thread["first_bad"]["git_sha"] == "bad2"
    assert thread["boundary_ref"] == {"kind": "commit", "value": "bad2"}


def test_finding_thread_keyed_to_event_ref_when_no_commit(session):
    # An Overnight, non-deploy regression has no commit — its boundary keys to the
    # non-deploy event ref instead (ADR-0015).
    _series(session, "metastore drift", [
        {"verdict": RunStatus.PASS},
        {"verdict": RunStatus.FAIL, "bucket": "PERMISSION_DENIED", "stage": "AUTHORIZE",
         "event_ref": "metastore grant change · 21:14"},
    ])

    finding = _lane(findings.build_feed(session), LANE_OVERNIGHT)["findings"][0]
    assert finding["provenance"]["boundary_ref"] == {
        "kind": "event", "value": "metastore grant change · 21:14"}


def test_finding_thread_has_no_boundary_ref_without_commit_or_event(session):
    # First-bad with neither a real commit sha nor an event ref: the thread still
    # exists (last-green → first-bad) but has no boundary key to diff against.
    _series(session, "unkeyed drift", [
        {"verdict": RunStatus.PASS},
        {"verdict": RunStatus.FAIL, "bucket": "OPERATION_FAILED", "stage": "OPERATE"},
    ])

    finding = _lane(findings.build_feed(session), LANE_OVERNIGHT)["findings"][0]
    assert finding["provenance"]["first_bad"] is not None
    assert finding["provenance"]["boundary_ref"] is None


def test_thread_last_green_honest_when_it_predates_retention_window(session):
    # The last green is 120 days old — far outside the 90-day window — with a
    # recent fail. After the sweep prunes the window, the preserved green anchor
    # keeps the thread honest: it still diffs the RIGHT last-good, not the edge.
    check, target = _series(session, "long stable then broke", [
        {"verdict": RunStatus.PASS, "git_sha": "anchor-green",
         "minutes_ago": 60 * 24 * 120},
        {"verdict": RunStatus.FAIL, "git_sha": "recent-bad",
         "bucket": "OPERATION_FAILED", "stage": "OPERATE", "minutes_ago": 30},
    ])

    pruned = retention.sweep(session)
    assert pruned == 0  # the old green is the preserved anchor, not pruned

    finding = _lane(findings.build_feed(session), LANE_OVERNIGHT)["findings"][0]
    thread = finding["provenance"]
    assert thread["last_green"]["git_sha"] == "anchor-green"
    assert thread["first_bad"]["git_sha"] == "recent-bad"


def test_flaky_finding_is_flagged_but_stays_advisory(session):
    # An alternating (green/bad) check whose current state is bad is a finding,
    # flagged flaky — but flaky is advisory: it never flips gating.
    _series(session, "flaky check", [
        {"verdict": RunStatus.PASS},
        {"verdict": RunStatus.FAIL, "bucket": "OPERATION_FAILED", "stage": "OPERATE"},
        {"verdict": RunStatus.PASS},
        {"verdict": RunStatus.FAIL, "bucket": "OPERATION_FAILED", "stage": "OPERATE"},
    ])

    finding = _lane(findings.build_feed(session), LANE_OVERNIGHT)["findings"][0]
    assert finding["flaky"] is True
    # Advisory: the deterministic verdict still gates on its own terms; flaky did
    # not turn it into (nor demote it from) a gating regression.
    assert finding["gating"] is True


def test_clean_regression_finding_is_not_flaky(session):
    _series(session, "clean regression", [
        {"verdict": RunStatus.PASS},
        {"verdict": RunStatus.FAIL, "bucket": "OPERATION_FAILED", "stage": "OPERATE"},
    ])

    finding = _lane(findings.build_feed(session), LANE_OVERNIGHT)["findings"][0]
    assert finding["flaky"] is False
