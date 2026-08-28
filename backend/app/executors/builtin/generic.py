"""Generic open-SDK handler + the unsupported-handler fallback.

Under open-SDK authoring (ADR-0017) most Checks don't need a bespoke handler:
they express an arbitrary Databricks SDK/API call, and this generic handler
walks the full canonical spine for it — authenticate, authorize, then perform
the call at OPERATE and check the response at VERIFY.

In demo/local mode (no Databricks binding) the stages are simulated from the
Check's ``params`` so the platform is fully exercisable without a workspace:

* ``demo_outcome``  — ``"pass"`` (default) | ``"deny"`` (denied at AUTHORIZE,
  the counterfactual path) | ``"fail"`` (operation fails at OPERATE).
* ``demo_evidence`` — dict merged into the OPERATE evidence (row_count, ...).
"""
from __future__ import annotations

from app.core.enums import ProbeOutcome
from app.executors.base import Handler, HandlerContext, HandlerError, StageResult
from app.executors.registry import register


def _demo_outcome(ctx: HandlerContext) -> str:
    return str(ctx.param("demo_outcome", "pass")).lower()


class _SpineMixin:
    """Shared connectivity + verify stages used by the SDK-shaped handlers."""

    def stage_authenticate(self, ctx: HandlerContext) -> StageResult:
        if ctx.demo_mode:
            return StageResult("AUTHENTICATE", ProbeOutcome.PASS, message="Demo mode: identity simulated.")
        try:
            client = ctx.workspace_client_factory()
            me = client.current_user.me()
            who = getattr(me, "user_name", "service-principal")
            return StageResult("AUTHENTICATE", ProbeOutcome.PASS, message=f"Authenticated as {who}.")
        except Exception as exc:  # noqa: BLE001
            raise HandlerError(f"Authentication failed: {exc}", failure_bucket="AUTHENTICATION_FAILURE")

    def stage_authorize(self, ctx: HandlerContext) -> StageResult:
        # The identity is the primary blast-radius bound (ADR-0017): a call the
        # identity isn't granted is denied here. In demo we simulate that denial
        # for the counterfactual path.
        if _demo_outcome(ctx) == "deny":
            raise HandlerError(
                "Operation denied for the effective identity (fails closed).",
                failure_bucket="PERMISSION_DENIED",
            )
        return StageResult("AUTHORIZE", ProbeOutcome.PASS, message="Identity is authorized for this operation.")

    def stage_verify(self, ctx: HandlerContext) -> StageResult:
        return StageResult("VERIFY", ProbeOutcome.PASS, message="Response verified.")


@register("databricks_sdk")
class DatabricksSdkHandler(_SpineMixin, Handler):
    """Perform an arbitrary Databricks SDK/API call across the full spine."""

    STAGES = ("AUTHENTICATE", "AUTHORIZE", "OPERATE", "VERIFY")

    def stage_operate(self, ctx: HandlerContext) -> StageResult:
        outcome = _demo_outcome(ctx)
        evidence = {"operation": ctx.operation.qualified, **dict(ctx.param("demo_evidence", {}))}
        if outcome == "fail":
            raise HandlerError(
                f"Operation '{ctx.operation.qualified}' failed.",
                failure_bucket="OPERATION_FAILED",
            )
        return StageResult(
            "OPERATE", ProbeOutcome.PASS,
            message=f"Performed {ctx.operation.qualified or 'SDK call'}.",
            evidence=evidence,
            raw_excerpt=str(ctx.operation.to_dict()),
        )


class UnsupportedHandler(Handler):
    """Fallback when no handler is registered for a Check's ``handler_key``."""

    STAGES = ("AUTHORIZE",)

    def stage_authorize(self, ctx: HandlerContext) -> StageResult:
        return StageResult(
            "AUTHORIZE", ProbeOutcome.SKIP,
            message=f"No handler registered for '{getattr(ctx.check, 'handler_key', '?')}'.",
            failure_bucket="SERVICE_UNSUPPORTED",
        )
