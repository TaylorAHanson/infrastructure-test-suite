"""SQL warehouse handler — runs a statement and verifies the result.

Maps onto AUTHENTICATE → AUTHORIZE → OPERATE → VERIFY (SQL traffic stays on the
Databricks control/data plane, so RESOLVE/CONNECT/TLS are implicit). In demo
mode the statement is simulated and a ``row_count`` is emitted as evidence so
row-count assertions ("returns at least one row") can be evaluated locally.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.enums import ProbeOutcome
from app.executors.base import HandlerContext, HandlerError, StageResult
from app.executors.builtin.generic import DatabricksSdkHandler, _demo_outcome
from app.executors.registry import register


@register("sql_warehouse")
class SqlWarehouseHandler(DatabricksSdkHandler):
    STAGES = ("AUTHENTICATE", "AUTHORIZE", "OPERATE", "VERIFY")

    def _warehouse_id(self, ctx: HandlerContext):
        return ctx.param("warehouse_id") or settings.DATABRICKS_WAREHOUSE_ID

    def stage_operate(self, ctx: HandlerContext) -> StageResult:
        statement = ctx.param("statement") or ctx.param("query") or "SELECT 1"
        if _demo_outcome(ctx) == "fail":
            raise HandlerError(f"Statement failed: {statement}", failure_bucket="OPERATION_FAILED")
        if ctx.demo_mode:
            row_count = int(ctx.param("demo_row_count", 1))
            return StageResult(
                "OPERATE", ProbeOutcome.PASS, message=f"Statement returned {row_count} row(s).",
                raw_excerpt=statement, evidence={"row_count": row_count, "statement": statement},
            )
        try:
            client = ctx.workspace_client_factory()
            resp = client.statement_execution.execute_statement(
                warehouse_id=self._warehouse_id(ctx), statement=statement, wait_timeout="30s",
            )
            rows = resp.result.data_array if (resp.result and resp.result.data_array) else []
            return StageResult(
                "OPERATE", ProbeOutcome.PASS, message=f"Statement returned {len(rows)} row(s).",
                raw_excerpt=statement, evidence={"row_count": len(rows), "first_row": rows[0] if rows else None},
            )
        except Exception as exc:  # noqa: BLE001
            raise HandlerError(f"Statement execution failed: {exc}", failure_bucket="OPERATION_FAILED")
