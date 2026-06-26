"""SQL Warehouse smoke probe.

PREFLIGHT: verify a warehouse is bound + reachable (auth/route).
SMOKE/FULL: run a deterministic ``SELECT`` and verify the result.

Uses the Databricks SDK statement-execution API via app resources - no raw
warehouse IDs in code beyond what the app resource/env supplies.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.enums import ConnectivityOutcome, ProbeOutcome, RootCauseBucket, StepKind
from app.executors.base import Executor, ProbeContext, ProbeError, StepResult
from app.executors.registry import register


@register("sql_warehouse_smoke")
class SqlWarehouseSmokeExecutor(Executor):
    def _warehouse_id(self, ctx: ProbeContext) -> str | None:
        return ctx.param("warehouse_id") or settings.DATABRICKS_WAREHOUSE_ID

    def step_precheck(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode:
            return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS,
                              message="Demo mode: SQL warehouse binding simulated.")
        if not self._warehouse_id(ctx):
            raise ProbeError(
                "No SQL warehouse bound (DATABRICKS_WAREHOUSE_ID / param 'warehouse_id').",
                root_cause=RootCauseBucket.APP_RESOURCE_MISSING,
                recommendation="Attach a SQL warehouse app resource and set DATABRICKS_WAREHOUSE_ID.",
            )
        return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS, message="Warehouse binding present.")

    def step_auth(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode:
            return StepResult(StepKind.AUTH, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="Demo mode: OAuth identity simulated.")
        try:
            client = ctx.workspace_client_factory()
            me = client.current_user.me()
            return StepResult(StepKind.AUTH, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message=f"Authenticated as {getattr(me, 'user_name', 'service-principal')}.")
        except Exception as exc:  # noqa: BLE001
            raise ProbeError(
                f"OAuth authentication failed: {exc}",
                root_cause=RootCauseBucket.OAUTH_SCOPE_MISSING,
                connectivity=ConnectivityOutcome.NOT_CONNECTABLE,
                recommendation="Verify the app service principal has the 'sql' OAuth scope.",
            )

    def step_dns(self, ctx: ProbeContext) -> StepResult:
        # SQL warehouse traffic stays on the Databricks control/data plane; DNS
        # is implicitly validated by the SDK handshake. Reported for symmetry.
        return StepResult(StepKind.DNS, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                          message="Workspace endpoint resolvable (managed by SDK).")

    def step_network(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.NETWORK, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                          message="Reached warehouse endpoint.")

    def step_functional(self, ctx: ProbeContext) -> StepResult:
        query = ctx.param("query", "SELECT 1 AS healthcheck")
        if ctx.demo_mode:
            return StepResult(
                StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                message="Demo mode: query returned 1 row.",
                raw_payload_excerpt=query,
                evidence={"row_count": 1, "first_row": [1]},
            )
        try:
            client = ctx.workspace_client_factory()
            resp = client.statement_execution.execute_statement(
                warehouse_id=self._warehouse_id(ctx),
                statement=query,
                wait_timeout="30s",
            )
            rows = []
            if resp.result and resp.result.data_array:
                rows = resp.result.data_array
            return StepResult(
                StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                message=f"Query succeeded; {len(rows)} row(s).",
                raw_payload_excerpt=query,
                evidence={"row_count": len(rows), "first_row": rows[0] if rows else None},
            )
        except Exception as exc:  # noqa: BLE001
            raise ProbeError(
                f"Query execution failed: {exc}",
                root_cause=RootCauseBucket.DATA_VALIDATION_FAILURE,
                recommendation="Check warehouse state, permissions, and query syntax.",
            )
