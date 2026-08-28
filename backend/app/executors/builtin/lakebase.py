"""Lakebase (operational Postgres) handler — validates the app's own store path.

Maps onto AUTHENTICATE → OPERATE → VERIFY. Runs a trivial transaction against
the Lakebase-authoritative store (or the local SQLite in demo mode).
"""
from __future__ import annotations

from app.core.enums import ProbeOutcome
from app.executors.base import HandlerContext, HandlerError, StageResult
from app.executors.builtin.generic import DatabricksSdkHandler
from app.executors.registry import register


@register("lakebase")
class LakebaseHandler(DatabricksSdkHandler):
    STAGES = ("AUTHENTICATE", "OPERATE", "VERIFY")

    def stage_operate(self, ctx: HandlerContext) -> StageResult:
        from sqlalchemy import text

        from app.db.session import session_scope

        try:
            with session_scope() as session:
                value = session.execute(text("SELECT 1")).first()
            return StageResult("OPERATE", ProbeOutcome.PASS, message="Minimal transaction succeeded (SELECT 1).",
                               evidence={"value": list(value) if value else None, "row_count": 1})
        except Exception as exc:  # noqa: BLE001
            raise HandlerError(f"Lakebase transaction failed: {exc}", failure_bucket="OPERATION_FAILED")
