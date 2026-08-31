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

from collections.abc import Iterable
from typing import Optional

from app.core.enums import StageStatus
from app.handlers.base import Handler, HandlerContext, HandlerError, StageResult
from app.handlers.registry import register

#: Params that steer demo simulation / handler bookkeeping rather than the
#: real SDK call — stripped before an operation's ``params`` become call kwargs.
_CONTROL_PARAM_KEYS = {"demo_outcome", "demo_evidence", "demo_row_count", "cleanup"}
#: Cap on how many items of a list-shaped SDK response are kept as evidence.
_MAX_LIST_EVIDENCE_ITEMS = 25


def _demo_outcome(ctx: HandlerContext) -> str:
    return str(ctx.param("demo_outcome", "pass")).lower()


def _make_workspace_client(host: Optional[str], token: str):
    """Mint a ``WorkspaceClient`` bound to a per-target token (real mode only)."""
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(host=host, token=token)


def _dictify(value):
    if hasattr(value, "as_dict"):
        try:
            return value.as_dict()
        except Exception:  # noqa: BLE001 - fall back to a readable string
            return str(value)
    return value


def _response_evidence(response) -> dict:
    """Normalize an arbitrary SDK response into JSON-safe evidence."""
    if response is None:
        return {"response": None}
    if hasattr(response, "as_dict"):
        try:
            return {"response": response.as_dict()}
        except Exception:  # noqa: BLE001
            return {"response": str(response)}
    if isinstance(response, (str, bytes, int, float, bool, dict)):
        return {"response": response}
    if isinstance(response, Iterable):
        items = []
        count = 0
        for item in response:
            count += 1
            if len(items) < _MAX_LIST_EVIDENCE_ITEMS:
                items.append(_dictify(item))
        return {"response": items, "row_count": count}
    return {"response": _dictify(response)}


def _resolve_ref(value, evidence: dict):
    """Resolve a ``"$dotted.path"`` reference against OPERATE's evidence."""
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    current = evidence
    for part in value[1:].split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


class _SpineMixin:
    """Shared connectivity + verify stages used by the SDK-shaped handlers."""

    def _client(self, ctx: HandlerContext):
        """Build (or reuse) the client bound to the run's resolved identity.

        A per-target token (ADR-0003/0018) takes precedence — it's what makes
        the call happen *as* the target's SP / assumed role, not the app's own
        ambient identity. Falls back to ``workspace_client_factory`` only when
        no token was resolved (demo/local, or a target with no identity handle).
        """
        cached = getattr(self, "_ws_client", None)
        if cached is not None:
            return cached
        if ctx.token:
            host = ctx.target.host if ctx.target else None
            client = _make_workspace_client(host, ctx.token)
        else:
            client = ctx.workspace_client_factory()
        self._ws_client = client
        return client

    def stage_authenticate(self, ctx: HandlerContext) -> StageResult:
        if ctx.demo_mode:
            return StageResult("AUTHENTICATE", StageStatus.PASS, message="Demo mode: identity simulated.")
        try:
            client = self._client(ctx)
            me = client.current_user.me()
            who = getattr(me, "user_name", None) or "service-principal"
            suffix = f" (assumed role {ctx.assumed_group})" if ctx.assumed_group else ""
            return StageResult("AUTHENTICATE", StageStatus.PASS, message=f"Authenticated as {who}{suffix}.")
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
        return StageResult("AUTHORIZE", StageStatus.PASS, message="Identity is authorized for this operation.")

    def stage_verify(self, ctx: HandlerContext) -> StageResult:
        return StageResult("VERIFY", StageStatus.PASS, message="Response verified.")


@register("databricks_sdk")
class DatabricksSdkHandler(_SpineMixin, Handler):
    """Perform an arbitrary Databricks SDK/API call across the full spine."""

    STAGES = ("AUTHENTICATE", "AUTHORIZE", "OPERATE", "VERIFY")

    def stage_operate(self, ctx: HandlerContext) -> StageResult:
        if ctx.demo_mode:
            outcome = _demo_outcome(ctx)
            evidence = {"operation": ctx.operation.qualified, **dict(ctx.param("demo_evidence", {}))}
            if outcome == "fail":
                raise HandlerError(
                    f"Operation '{ctx.operation.qualified}' failed.",
                    failure_bucket="OPERATION_FAILED",
                )
            return StageResult(
                "OPERATE", StageStatus.PASS,
                message=f"Performed {ctx.operation.qualified or 'SDK call'}.",
                evidence=evidence,
                raw_excerpt=str(ctx.operation.to_dict()),
            )

        from databricks.sdk.errors import DatabricksError, PermissionDenied, Unauthenticated

        client = self._client(ctx)
        service = getattr(client, ctx.operation.service, None)
        method = getattr(service, ctx.operation.method, None) if service is not None else None
        if method is None:
            raise HandlerError(
                f"SDK has no '{ctx.operation.qualified}' operation.",
                failure_bucket="SERVICE_UNSUPPORTED",
            )
        call_params = {k: v for k, v in ctx.operation.params.items() if k not in _CONTROL_PARAM_KEYS}
        try:
            response = method(**call_params)
        except (PermissionDenied, Unauthenticated) as exc:
            raise HandlerError(
                f"Operation '{ctx.operation.qualified}' denied for the effective identity: {exc}",
                failure_bucket="PERMISSION_DENIED",
            )
        except DatabricksError as exc:
            raise HandlerError(f"Operation '{ctx.operation.qualified}' failed: {exc}", failure_bucket="OPERATION_FAILED")
        except Exception as exc:  # noqa: BLE001 - classify everything, never crash a run
            raise HandlerError(f"Operation '{ctx.operation.qualified}' failed: {exc}", failure_bucket="OPERATION_FAILED")

        evidence = {"operation": ctx.operation.qualified, **_response_evidence(response)}
        self._operate_ok = True
        self._operate_evidence = evidence
        return StageResult(
            "OPERATE", StageStatus.PASS,
            message=f"Performed {ctx.operation.qualified}.",
            evidence=evidence,
            raw_excerpt=str(response)[:2000],
        )

    def cleanup(self, ctx: HandlerContext) -> Optional[StageResult]:
        """Tear down a synthetic artifact OPERATE created, if the Check declared one.

        Opt-in via a ``cleanup`` param — ``{"service", "method", "params"}`` —
        authored alongside the real operation, mirroring ``demo_evidence``.
        ``params`` values of the form ``"$path.into.evidence"`` are resolved
        against OPERATE's evidence (e.g. the created resource's id).
        """
        if ctx.demo_mode or not getattr(self, "_operate_ok", False):
            return None
        plan = ctx.param("cleanup")
        if not isinstance(plan, dict) or not plan.get("service") or not plan.get("method"):
            return None
        client = self._client(ctx)
        service = getattr(client, plan["service"], None)
        method = getattr(service, plan["method"], None) if service is not None else None
        if method is None:
            raise HandlerError(
                f"Cleanup target '{plan['service']}.{plan['method']}' not found.",
                failure_bucket="CLEANUP_FAILURE",
            )
        evidence = getattr(self, "_operate_evidence", {}) or {}
        call_params = {k: _resolve_ref(v, evidence) for k, v in dict(plan.get("params", {})).items()}
        method(**call_params)
        return StageResult(
            "VERIFY", StageStatus.PASS,
            message=f"Cleaned up via {plan['service']}.{plan['method']}.",
        )


class UnsupportedHandler(Handler):
    """Fallback when no handler is registered for a Check's ``handler_key``."""

    STAGES = ("AUTHORIZE",)

    def stage_authorize(self, ctx: HandlerContext) -> StageResult:
        return StageResult(
            "AUTHORIZE", StageStatus.SKIP,
            message=f"No handler registered for '{getattr(ctx.check, 'handler_key', '?')}'.",
            failure_bucket="SERVICE_UNSUPPORTED",
        )
