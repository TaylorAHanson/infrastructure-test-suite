"""Handler contract: stage-spine execution, depth, short-circuit, cleanup."""
from __future__ import annotations

from app.core.enums import Depth, StageStatus
from app.core.operations import Operation
from app.core.stages import connectivity_stage_ids, ordered_stages
from app.handlers.base import Handler, HandlerContext, HandlerError, StageResult
from app.handlers.builtin.sql_warehouse import SqlWarehouseHandler


def _ctx(depth, params=None):
    params = params or {}
    return HandlerContext(
        run_id="r", operation=Operation("svc", "list", params=params),
        depth=depth, params=params, demo_mode=True,
    )


def test_quick_runs_only_connectivity_stages():
    steps = SqlWarehouseHandler().run(_ctx(Depth.QUICK))
    ids = [s.stage for s in steps]
    assert ids, "quick run should still probe connectivity"
    assert all(sid in connectivity_stage_ids() for sid in ids)
    assert "OPERATE" not in ids and "VERIFY" not in ids
    assert "AUTHENTICATE" in ids


def test_full_runs_operate_and_verify():
    steps = SqlWarehouseHandler().run(_ctx(Depth.FULL, {"demo_row_count": 2}))
    ids = [s.stage for s in steps]
    assert "OPERATE" in ids and "VERIFY" in ids
    operate = next(s for s in steps if s.stage == "OPERATE")
    assert operate.outcome == StageStatus.PASS
    assert operate.evidence.get("row_count") == 2


def test_stages_emit_in_canonical_spine_order():
    steps = SqlWarehouseHandler().run(_ctx(Depth.FULL))
    order = {s.id: s.order for s in ordered_stages()}
    emitted = [order[s.stage] for s in steps]
    assert emitted == sorted(emitted)


def test_failed_connectivity_stage_short_circuits():
    class _Failing(Handler):
        STAGES = ("AUTHENTICATE", "OPERATE")

        def stage_authenticate(self, ctx):
            raise HandlerError("no identity", failure_bucket="AUTHENTICATION_FAILURE")

        def stage_operate(self, ctx):  # must never run
            raise AssertionError("operate ran despite a failed authenticate")

    steps = _Failing().run(_ctx(Depth.FULL))
    assert steps[0].stage == "AUTHENTICATE" and steps[0].outcome == StageStatus.FAIL
    assert steps[0].failure_bucket == "AUTHENTICATION_FAILURE"
    assert all(s.stage != "OPERATE" for s in steps)


def test_cleanup_runs_even_when_a_stage_fails():
    seen = {}

    class _Handler(Handler):
        STAGES = ("OPERATE",)

        def stage_operate(self, ctx):
            raise HandlerError("boom", failure_bucket="OPERATION_FAILED")

        def cleanup(self, ctx):
            seen["cleaned"] = True
            return StageResult("VERIFY", StageStatus.PASS, message="cleaned up")

    _Handler().run(_ctx(Depth.FULL))
    assert seen.get("cleaned") is True
