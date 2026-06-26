"""Executor contract tests: step ordering, modes, connectable vs functional."""
from __future__ import annotations

from app.core.enums import CleanupPolicy, ExecutionMode, ProbeOutcome, StepKind
from app.executors import ProbeContext
from app.executors.builtin.sql_warehouse import SqlWarehouseSmokeExecutor


class _Def:
    cleanup_policy = CleanupPolicy.NEVER
    enterprise_only = False
    executor_key = "sql_warehouse_smoke"


def _ctx(mode):
    return ProbeContext(
        run_id="run_test", definition=_Def(), execution_mode=mode,
        parameters={"query": "SELECT 1"}, demo_mode=True,
    )


def test_preflight_skips_functional():
    steps = SqlWarehouseSmokeExecutor().run(_ctx(ExecutionMode.PREFLIGHT))
    kinds = [s.step_kind for s in steps]
    assert StepKind.FUNCTIONAL not in kinds
    assert StepKind.AUTH in kinds and StepKind.NETWORK in kinds


def test_smoke_runs_functional_and_passes_in_demo():
    steps = SqlWarehouseSmokeExecutor().run(_ctx(ExecutionMode.SMOKE))
    functional = [s for s in steps if s.step_kind == StepKind.FUNCTIONAL]
    assert functional and functional[0].outcome == ProbeOutcome.PASS


def test_cleanup_runs_when_policy_requires(monkeypatch):
    class _DefClean(_Def):
        cleanup_policy = CleanupPolicy.ALWAYS

    ctx = ProbeContext(run_id="r", definition=_DefClean(), execution_mode=ExecutionMode.SMOKE,
                       parameters={}, demo_mode=True)
    steps = SqlWarehouseSmokeExecutor().run(ctx)
    assert any(s.step_kind == StepKind.CLEANUP for s in steps)


def test_connectable_short_circuits_on_failure():
    """A failing precheck stops before functional work."""
    from app.executors.base import Executor, ProbeError, StepResult
    from app.core.enums import RootCauseBucket

    class _Failing(Executor):
        def step_precheck(self, ctx):
            raise ProbeError("missing", root_cause=RootCauseBucket.APP_RESOURCE_MISSING)

        def step_functional(self, ctx):  # should never run
            raise AssertionError("functional ran despite failed precheck")

    steps = _Failing().run(_ctx(ExecutionMode.SMOKE))
    assert steps[0].step_kind == StepKind.PRECHECK
    assert steps[0].outcome == ProbeOutcome.FAIL
    assert all(s.step_kind != StepKind.FUNCTIONAL for s in steps)
