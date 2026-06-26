"""Executor base class implementing the six-question probe contract.

Every probe answers, in order:

1. PRECHECK   - is config/credential/permission present?
2. AUTH       - can I authenticate?
3. DNS        - can I resolve the target?
4. NETWORK    - can I establish the network path / handshake?
5. FUNCTIONAL - can I perform the minimum useful operation and verify it?
6. CLEANUP    - can I clean up any temporary artifacts?

The base class orchestrates these according to the requested
:class:`ExecutionMode` and guarantees cleanup runs in a ``finally`` block.

* **Connectable** = PRECHECK + AUTH + DNS + NETWORK all non-failing.
* **Functional**  = FUNCTIONAL passes.

Subclasses override only the steps they support; everything else degrades to a
clean SKIP. This keeps "add a new test" to mostly config + a small plugin.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.core.enums import (
    ConnectivityOutcome,
    ExecutionMode,
    ProbeOutcome,
    RootCauseBucket,
    StepKind,
)
from app.core.ids import utcnow


class ProbeError(Exception):
    """An error carrying a normalized root-cause classification."""

    def __init__(
        self,
        message: str,
        root_cause: RootCauseBucket = RootCauseBucket.UNKNOWN,
        recommendation: Optional[str] = None,
        connectivity: ConnectivityOutcome = ConnectivityOutcome.UNKNOWN,
    ) -> None:
        super().__init__(message)
        self.root_cause = root_cause
        self.recommendation = recommendation
        self.connectivity = connectivity


@dataclass
class StepResult:
    step_kind: StepKind
    outcome: ProbeOutcome = ProbeOutcome.SKIP
    connectivity: ConnectivityOutcome = ConnectivityOutcome.UNKNOWN
    message: str = ""
    error_class: Optional[str] = None
    root_cause_bucket: Optional[RootCauseBucket] = None
    recommendation: Optional[str] = None
    raw_payload_excerpt: Optional[str] = None
    evidence: dict = field(default_factory=dict)
    duration_ms: Optional[int] = None
    started_at: Optional[Any] = None
    finished_at: Optional[Any] = None

    @property
    def failed(self) -> bool:
        return self.outcome in (ProbeOutcome.FAIL, ProbeOutcome.ERROR)


@dataclass
class ProbeContext:
    """Everything an executor needs to run one probe against one target."""

    run_id: str
    definition: Any  # TestDefinition
    target: Any = None  # TestTarget | None
    execution_mode: ExecutionMode = ExecutionMode.SMOKE
    parameters: dict = field(default_factory=dict)
    # Factory so executors get a fresh authed client only when needed.
    workspace_client_factory: Optional[Callable[[], Any]] = None
    # Set True when no real Databricks binding exists (local/demo mode).
    demo_mode: bool = False
    logger: Any = None

    def param(self, key: str, default: Any = None) -> Any:
        return self.parameters.get(key, default)


def _excerpt(value: Any, limit: int = 2000) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text[:limit]


class Executor:
    """Base executor. Subclass and override the relevant ``step_*`` methods."""

    #: Registry key (set via :func:`app.executors.registry.register`).
    executor_key: str = "base"

    # ---- Steps (override in subclasses) --------------------------------
    def step_precheck(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS, ConnectivityOutcome.UNKNOWN,
                          message="No precheck implemented.")

    def step_auth(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.AUTH, ProbeOutcome.SKIP, ConnectivityOutcome.UNKNOWN,
                          message="No auth step implemented.")

    def step_dns(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.DNS, ProbeOutcome.SKIP, ConnectivityOutcome.UNKNOWN,
                          message="No DNS step implemented.")

    def step_network(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.NETWORK, ProbeOutcome.SKIP, ConnectivityOutcome.UNKNOWN,
                          message="No network step implemented.")

    def step_functional(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.FUNCTIONAL, ProbeOutcome.SKIP, ConnectivityOutcome.UNKNOWN,
                          message="No functional step implemented.")

    def step_cleanup(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.CLEANUP, ProbeOutcome.SKIP, ConnectivityOutcome.UNKNOWN,
                          message="Nothing to clean up.")

    # ---- Orchestration -------------------------------------------------
    # Steps that make up "connectable".
    _CONNECTABLE_STEPS = (StepKind.PRECHECK, StepKind.AUTH, StepKind.DNS, StepKind.NETWORK)

    def _ordered_steps(self, mode: ExecutionMode) -> list[StepKind]:
        connectable = list(self._CONNECTABLE_STEPS)
        if mode == ExecutionMode.PREFLIGHT:
            return connectable
        return connectable + [StepKind.FUNCTIONAL]

    def _dispatch(self, kind: StepKind, ctx: ProbeContext) -> StepResult:
        return {
            StepKind.PRECHECK: self.step_precheck,
            StepKind.AUTH: self.step_auth,
            StepKind.DNS: self.step_dns,
            StepKind.NETWORK: self.step_network,
            StepKind.FUNCTIONAL: self.step_functional,
            StepKind.CLEANUP: self.step_cleanup,
        }[kind](ctx)

    def _run_one(self, kind: StepKind, ctx: ProbeContext) -> StepResult:
        started = utcnow()
        t0 = time.monotonic()
        try:
            result = self._dispatch(kind, ctx)
        except ProbeError as exc:
            result = StepResult(
                step_kind=kind,
                outcome=ProbeOutcome.FAIL,
                connectivity=exc.connectivity,
                message=str(exc),
                error_class=type(exc).__name__,
                root_cause_bucket=exc.root_cause,
                recommendation=exc.recommendation,
            )
        except Exception as exc:  # noqa: BLE001 - we classify everything
            result = StepResult(
                step_kind=kind,
                outcome=ProbeOutcome.ERROR,
                connectivity=ConnectivityOutcome.UNKNOWN,
                message=str(exc),
                error_class=type(exc).__name__,
                root_cause_bucket=RootCauseBucket.UNKNOWN,
                raw_payload_excerpt=_excerpt(traceback.format_exc()),
            )
        result.step_kind = kind
        result.started_at = started
        result.finished_at = utcnow()
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        return result

    def run(self, ctx: ProbeContext) -> list[StepResult]:
        """Run the probe, returning ordered step results.

        Cleanup always runs (finally) when the cleanup policy is not NEVER, so
        synthetic artifacts never leak even if a step raises.
        """
        results: list[StepResult] = []
        try:
            for kind in self._ordered_steps(ctx.execution_mode):
                step = self._run_one(kind, ctx)
                results.append(step)
                # Short-circuit functional work if we're not connectable.
                if kind in self._CONNECTABLE_STEPS and step.failed:
                    break
        finally:
            from app.core.enums import CleanupPolicy

            policy = getattr(ctx.definition, "cleanup_policy", CleanupPolicy.ALWAYS)
            functional_failed = any(
                r.step_kind == StepKind.FUNCTIONAL and r.failed for r in results
            )
            should_clean = policy != CleanupPolicy.NEVER and not (
                policy == CleanupPolicy.ON_SUCCESS and functional_failed
            )
            if should_clean:
                results.append(self._run_one(StepKind.CLEANUP, ctx))
        return results
