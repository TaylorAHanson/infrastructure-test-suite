"""Remote-agent executor (control-plane side).

Some connectivity can only be proven from outside the Databricks App: a user
desktop, a corporate network, a BI tool host, or an on-prem system. A run from
inside the app cannot prove that outside-in path.

For those tests the app does not execute the probe itself. Instead it:

1. marks the run as awaiting a remote agent at the relevant vantage point, and
2. accepts a **signed** result callback (HMAC-SHA256) at
   ``POST /api/v1/agents/results``, verified in :mod:`app.services.run_service`.

This executor produces a single PARTIAL/SKIP step describing what evidence is
required. The first iteration ships only the protocol + this stub; real agents
are separate executors deployed at the vantage point.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from app.core.config import settings
from app.core.enums import ConnectivityOutcome, ProbeOutcome, RootCauseBucket, StepKind
from app.executors.base import Executor, ProbeContext, StepResult
from app.executors.registry import register


def canonical_payload(run_id: str, agent_id: str, steps: list[dict]) -> str:
    """Canonical JSON used for HMAC signing/verification (stable key order)."""
    return json.dumps(
        {"run_id": run_id, "agent_id": agent_id, "steps": steps},
        sort_keys=True,
        separators=(",", ":"),
    )


def verify_signature(secret: str, run_id: str, agent_id: str, steps: list[dict], signature: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        canonical_payload(run_id, agent_id, steps).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@register("remote_agent")
class RemoteAgentExecutor(Executor):
    def step_precheck(self, ctx: ProbeContext) -> StepResult:
        vantage = ctx.param("vantage_point", "external")
        return StepResult(
            StepKind.PRECHECK,
            ProbeOutcome.PARTIAL,
            ConnectivityOutcome.UNKNOWN,
            message=(
                f"This probe requires a remote agent at vantage point '{vantage}'. "
                "The app cannot prove outside-in connectivity by itself."
            ),
            root_cause_bucket=RootCauseBucket.EXTERNAL_AGENT_UNAVAILABLE,
            recommendation=(
                "Deploy/register a remote agent at the vantage point; it will post "
                "signed results to POST /api/v1/agents/results."
            ),
            evidence={
                "requires_remote_agent": True,
                "vantage_point": vantage,
                "signing": "HMAC-SHA256",
                "signing_secret_configured": bool(settings.REMOTE_AGENT_SIGNING_SECRET),
            },
        )

    def step_functional(self, ctx: ProbeContext) -> StepResult:
        return StepResult(
            StepKind.FUNCTIONAL,
            ProbeOutcome.SKIP,
            ConnectivityOutcome.UNKNOWN,
            message="Awaiting signed result from remote agent (passive validation).",
        )
