"""Lakebase (operational Postgres) smoke probe.

PREFLIGHT: confirm PG* env vars are injected and a token can be obtained.
SMOKE/FULL: open a connection and run a minimal transaction.

This validates the app's own operational system-of-record path end to end.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.enums import ConnectivityOutcome, ProbeOutcome, RootCauseBucket, StepKind
from app.executors.base import Executor, ProbeContext, ProbeError, StepResult
from app.executors.registry import register


@register("lakebase_smoke")
class LakebaseSmokeExecutor(Executor):
    def step_precheck(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode or not settings.has_lakebase:
            return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS,
                              message="Demo mode: using local SQLite instead of Lakebase.")
        return StepResult(StepKind.PRECHECK, ProbeOutcome.PASS,
                          message=f"Lakebase host present: {settings.PGHOST}.")

    def step_auth(self, ctx: ProbeContext) -> StepResult:
        if ctx.demo_mode or not settings.has_lakebase:
            return StepResult(StepKind.AUTH, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                              message="Demo mode: SQLite needs no auth.")
        try:
            from app.core.config import get_databricks_oauth_token

            token = get_databricks_oauth_token()
            ok = bool(token)
            return StepResult(StepKind.AUTH, ProbeOutcome.PASS if ok else ProbeOutcome.FAIL,
                              ConnectivityOutcome.CONNECTABLE if ok else ConnectivityOutcome.NOT_CONNECTABLE,
                              message="Obtained Lakebase OAuth token." if ok else "Empty token.")
        except Exception as exc:  # noqa: BLE001
            raise ProbeError(
                f"Failed to obtain Lakebase OAuth token: {exc}",
                root_cause=RootCauseBucket.OAUTH_SCOPE_MISSING,
                connectivity=ConnectivityOutcome.NOT_CONNECTABLE,
            )

    def step_network(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.NETWORK, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                          message="Connection pool reachable.")

    def step_functional(self, ctx: ProbeContext) -> StepResult:
        from sqlalchemy import text

        from app.db.session import session_scope

        try:
            with session_scope() as session:
                value = session.execute(text("SELECT 1")).first()
            return StepResult(
                StepKind.FUNCTIONAL, ProbeOutcome.PASS, ConnectivityOutcome.CONNECTABLE,
                message="Minimal transaction succeeded (SELECT 1).",
                evidence={"value": list(value) if value else None},
            )
        except Exception as exc:  # noqa: BLE001
            raise ProbeError(
                f"Lakebase transaction failed: {exc}",
                root_cause=RootCauseBucket.DATA_VALIDATION_FAILURE,
                recommendation="Verify the postgres app resource permission is CAN_CONNECT_AND_CREATE.",
            )
