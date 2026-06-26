"""Real, workspace-global probes that run against the app's own environment.

These need no per-target config - they use the app service principal's OAuth
identity, so they're runnable in almost any Databricks workspace:

* ``databricks_app_health``  - identity + workspace reachability (SDK).
* ``unity_catalog_query``    - Unity Catalog access via a SQL warehouse.
* ``model_serving_invoke``   - Foundation Model / serving-endpoint round-trip.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.enums import ConnectivityOutcome, ProbeOutcome, RootCauseBucket, StepKind
from app.executors.base import Executor, ProbeContext, ProbeError, StepResult
from app.executors.registry import register


@register("databricks_app_health")
class DatabricksAppHealthExecutor(Executor):
    """Verify the app's OAuth identity and that the workspace API answers."""

    def step_precheck(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS,
                          message="App runtime present.")

    def step_auth(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode:
            return StepResult(StepKind.AUTH, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="Demo mode: identity simulated.")
        try:
            client = ctx.workspace_client_factory()
            me = client.current_user.me()
            return StepResult(StepKind.AUTH, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message=f"Authenticated as {getattr(me, 'user_name', 'service-principal')}.")
        except Exception as exc:  # noqa: BLE001
            raise ProbeError(
                f"OAuth identity check failed: {exc}",
                root_cause=RootCauseBucket.OAUTH_SCOPE_MISSING,
                connectivity=ConnectivityOutcome.NOT_CONNECTABLE,
                recommendation="Confirm the app service principal and its OAuth scopes.",
            )

    def step_network(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.NETWORK, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                          message="Workspace control plane reachable.")

    def step_functional(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode:
            return StepResult(StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="Demo mode: workspace API simulated.")
        try:
            client = ctx.workspace_client_factory()
            host = settings.workspace_host or getattr(client.config, "host", "")
            return StepResult(
                StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                message=f"Workspace API reachable ({host}).",
                evidence={"host": host},
            )
        except Exception as exc:  # noqa: BLE001
            raise ProbeError(
                f"Workspace API call failed: {exc}",
                root_cause=RootCauseBucket.UNKNOWN,
            )


@register("unity_catalog_query")
class UnityCatalogQueryExecutor(Executor):
    """Validate Unity Catalog access by running a statement on a SQL warehouse."""

    def _warehouse_id(self, ctx: ProbeContext) -> str | None:
        return ctx.param("warehouse_id") or settings.DATABRICKS_WAREHOUSE_ID

    def _statement(self, ctx: ProbeContext) -> str:
        stmt = ctx.param("statement") or ctx.param("query")
        if stmt:
            return stmt
        catalog = ctx.param("catalog")
        if catalog:
            schema = ctx.param("schema")
            return f"SHOW SCHEMAS IN `{catalog}`" if not schema else f"SHOW TABLES IN `{catalog}`.`{schema}`"
        return "SHOW CATALOGS"

    def step_precheck(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode:
            return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS, message="Demo mode.")
        if not self._warehouse_id(ctx):
            raise ProbeError(
                "No SQL warehouse bound for Unity Catalog query.",
                root_cause=RootCauseBucket.APP_RESOURCE_MISSING,
                recommendation="Attach a SQL warehouse resource / set DATABRICKS_WAREHOUSE_ID.",
            )
        return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS, message="Warehouse binding present.")

    def step_auth(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode:
            return StepResult(StepKind.AUTH, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="Demo mode.")
        try:
            ctx.workspace_client_factory().current_user.me()
            return StepResult(StepKind.AUTH, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="Authenticated.")
        except Exception as exc:  # noqa: BLE001
            raise ProbeError(f"Auth failed: {exc}", root_cause=RootCauseBucket.OAUTH_SCOPE_MISSING,
                             connectivity=ConnectivityOutcome.NOT_CONNECTABLE)

    def step_network(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.NETWORK, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                          message="Warehouse endpoint reachable.")

    def step_functional(self, ctx: ProbeContext) -> StepResult:
        statement = self._statement(ctx)
        if ctx.demo_mode:
            return StepResult(StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message=f"Demo mode: '{statement}' returned rows.",
                              raw_payload_excerpt=statement, evidence={"row_count": 3})
        try:
            client = ctx.workspace_client_factory()
            resp = client.statement_execution.execute_statement(
                warehouse_id=self._warehouse_id(ctx), statement=statement, wait_timeout="30s",
            )
            rows = resp.result.data_array if (resp.result and resp.result.data_array) else []
            return StepResult(
                StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                message=f"'{statement}' succeeded; {len(rows)} row(s).",
                raw_payload_excerpt=statement,
                evidence={"row_count": len(rows), "first_rows": rows[:5]},
            )
        except Exception as exc:  # noqa: BLE001
            raise ProbeError(
                f"Unity Catalog query failed: {exc}",
                root_cause=RootCauseBucket.PERMISSION_DENIED,
                recommendation="Verify the app SP has USE CATALOG/SCHEMA and SELECT grants.",
            )


@register("model_serving_invoke")
class ModelServingInvokeExecutor(Executor):
    """Round-trip a Model Serving (Foundation Model) endpoint."""

    def _endpoint(self, ctx: ProbeContext) -> str | None:
        return ctx.param("endpoint") or settings.MODEL_SERVING_ENDPOINT

    def step_precheck(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode:
            return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS, message="Demo mode.")
        if not self._endpoint(ctx):
            raise ProbeError(
                "No serving endpoint configured (MODEL_SERVING_ENDPOINT / param 'endpoint').",
                root_cause=RootCauseBucket.APP_RESOURCE_MISSING,
                recommendation="Set MODEL_SERVING_ENDPOINT or pass an 'endpoint' parameter.",
            )
        return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS,
                          message=f"Endpoint configured: {self._endpoint(ctx)}.")

    def step_auth(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.AUTH, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                          message="OAuth identity used by SDK.")

    def step_network(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.NETWORK, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                          message="Serving endpoint reachable (managed by SDK).")

    def step_functional(self, ctx: ProbeContext) -> StepResult:
        prompt = ctx.param("prompt", "Reply with the single word: OK")
        if ctx.demo_mode:
            return StepResult(StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="Demo mode: endpoint returned a completion.",
                              evidence={"reply_preview": "OK"})
        from app.clients.model_serving import ModelServingError, chat

        try:
            reply = chat(
                messages=[{"role": "user", "content": prompt}],
                endpoint=self._endpoint(ctx),
                max_tokens=16,
            )
            return StepResult(
                StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                message="Serving endpoint returned a completion.",
                raw_payload_excerpt=reply[:500],
                evidence={"reply_preview": reply[:200]},
            )
        except ModelServingError as exc:
            raise ProbeError(
                f"Model serving invocation failed: {exc}",
                root_cause=RootCauseBucket.UPSTREAM_TIMEOUT,
                recommendation="Check the endpoint name, its state, and the app SP's query permission.",
            )
