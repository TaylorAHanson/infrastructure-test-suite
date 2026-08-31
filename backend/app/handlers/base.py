"""Handler base class — executes the stages a handler declares, in spine order.

The old base executor orchestrated a fixed six-question ladder
(PRECHECK→AUTH→DNS→NETWORK→FUNCTIONAL→CLEANUP). The rebuilt base is spine-driven
(ADR-0001): a handler declares which **canonical stages** (RESOLVE…VERIFY) it
maps onto, and the base runs exactly those, in spine order, emitting a
:class:`StageResult` per stage. It invents no stages and hardcodes no ladder.

* A **connectivity** stage failing (≤ AUTHORIZE) short-circuits the rest — you
  can't OPERATE against something you can't reach.
* **Depth** ``quick`` runs only the connectivity stages; ``full`` runs through
  OPERATE/VERIFY (ADR-0007).
* **Cleanup always runs** in a ``finally`` block, even if a stage raises.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.core.enums import CleanupPolicy, Depth, StageStatus
from app.core.ids import utcnow
from app.core.operations import Operation
from app.core.stages import ordered_stages


class HandlerError(Exception):
    """A stage failure carrying a failure-taxonomy bucket (a string id)."""

    def __init__(
        self,
        message: str,
        failure_bucket: str = "UNKNOWN",
        recommendation: Optional[str] = None,
        outcome: StageStatus = StageStatus.FAIL,
    ) -> None:
        super().__init__(message)
        self.failure_bucket = failure_bucket
        self.recommendation = recommendation
        self.outcome = outcome


@dataclass
class StageResult:
    """The outcome of one stage on the canonical spine."""

    stage: str
    outcome: StageStatus = StageStatus.SKIP
    message: str = ""
    failure_bucket: Optional[str] = None
    evidence: dict = field(default_factory=dict)
    raw_excerpt: Optional[str] = None
    duration_ms: Optional[int] = None
    started_at: Optional[Any] = None
    finished_at: Optional[Any] = None

    @property
    def failed(self) -> bool:
        return self.outcome.failed


@dataclass
class HandlerContext:
    """Everything a handler needs to run one Check against one Target."""

    run_id: str
    operation: Operation
    check: Any = None  # Check
    target: Any = None  # Target | None
    depth: Depth = Depth.QUICK
    params: dict = field(default_factory=dict)
    workspace_client_factory: Optional[Callable[[], Any]] = None
    demo_mode: bool = False
    logger: Any = None
    #: The run's effective identity for this Check (ADR-0003/0018): the per-target
    #: SP token, or a token minted with ``assume_group`` when a run-as-role is set.
    #: None in demo mode / when the target has no identity handle. Handlers use it
    #: to make the actual SDK call as ``(target, assumed-role?)`` (consumed in Spec 3).
    token: Optional[str] = None
    assumed_group: Optional[str] = None

    def param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


def _excerpt(value: Any, limit: int = 2000) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text[:limit]


class Handler:
    """Base handler. Declare ``STAGES`` and override the matching ``stage_*``.

    A handler maps onto the spine by listing the canonical stage ids it
    implements in :attr:`STAGES`; the base runs those (intersected with the
    run depth) in spine order. Everything else is a clean SKIP.
    """

    #: Registry key (set via :func:`app.handlers.registry.register`).
    handler_key: str = "base"
    #: Canonical stage ids this handler maps onto (spine order enforced by base).
    STAGES: tuple[str, ...] = ()
    #: Cleanup policy for any synthetic artifacts the handler creates.
    cleanup_policy: CleanupPolicy = CleanupPolicy.ALWAYS

    # ---- Stage methods (override the ones you declare in STAGES) --------
    # Each returns a StageResult. Not overriding one that's declared yields a
    # SKIP; raising HandlerError yields a FAIL with the given bucket.
    def cleanup(self, ctx: HandlerContext) -> Optional[StageResult]:
        """Tear down synthetic artifacts. Runs in ``finally``. Override if needed."""
        return None

    # ---- Orchestration -------------------------------------------------
    def _declared_stages(self, depth: Depth) -> list:
        declared = [s for s in ordered_stages() if s.id in self.STAGES]
        if depth == Depth.QUICK:
            declared = [s for s in declared if s.connectivity]
        return declared

    def _dispatch(self, stage_id: str, ctx: HandlerContext) -> StageResult:
        method = getattr(self, f"stage_{stage_id.lower()}", None)
        if method is None:
            return StageResult(stage_id, StageStatus.SKIP, message=f"No {stage_id} stage implemented.")
        return method(ctx)

    def _run_one(self, stage_id: str, ctx: HandlerContext) -> StageResult:
        started = utcnow()
        t0 = time.monotonic()
        try:
            result = self._dispatch(stage_id, ctx)
        except HandlerError as exc:
            result = StageResult(
                stage=stage_id, outcome=exc.outcome, message=str(exc),
                failure_bucket=exc.failure_bucket,
                evidence={"recommendation": exc.recommendation} if exc.recommendation else {},
            )
        except Exception as exc:  # noqa: BLE001 - classify everything, never crash a run
            result = StageResult(
                stage=stage_id, outcome=StageStatus.ERROR, message=str(exc),
                failure_bucket="UNKNOWN", raw_excerpt=_excerpt(traceback.format_exc()),
            )
        result.stage = stage_id
        result.started_at = started
        result.finished_at = utcnow()
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        return result

    def run(self, ctx: HandlerContext) -> list[StageResult]:
        """Run the declared stages in spine order; cleanup always runs."""
        results: list[StageResult] = []
        try:
            for stage in self._declared_stages(ctx.depth):
                result = self._run_one(stage.id, ctx)
                results.append(result)
                if result.failed:
                    break  # a failed stage makes every later stage meaningless
        finally:
            functional_failed = any(r.failed for r in results)
            should_clean = self.cleanup_policy != CleanupPolicy.NEVER and not (
                self.cleanup_policy == CleanupPolicy.ON_SUCCESS and functional_failed
            )
            if should_clean:
                cleaned = self._run_one_cleanup(ctx)
                if cleaned is not None:
                    results.append(cleaned)
        return results

    def _run_one_cleanup(self, ctx: HandlerContext) -> Optional[StageResult]:
        started = utcnow()
        t0 = time.monotonic()
        try:
            result = self.cleanup(ctx)
        except Exception as exc:  # noqa: BLE001
            result = StageResult("VERIFY", StageStatus.ERROR, message=f"Cleanup failed: {exc}",
                                 failure_bucket="CLEANUP_FAILURE")
        if result is None:
            return None
        result.started_at = started
        result.finished_at = utcnow()
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        return result
