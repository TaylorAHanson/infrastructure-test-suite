"""Capability-gated stub executors.

The full test catalog (Fivetran, IICS, Auto Loader, Oracle Federation, compute
kinds, Model Serving, Genie, Power BI, Tableau, TM1, ...) is seeded as metadata
now. Many of those probes need real bindings/agents that may not exist yet.

Rather than crash a run, an unregistered/unsupported probe resolves to a clean
SKIP that explains *why* it skipped and what it would need to run for real.
This is the metadata-driven extension point: implement a real executor later
and register it under the same ``executor_key`` to "light up" the test.
"""
from __future__ import annotations

from app.core.enums import ConnectivityOutcome, ProbeOutcome, RootCauseBucket, StepKind
from app.executors.base import Executor, ProbeContext, StepResult
from app.executors.registry import register


class CapabilityGatedStub(Executor):
    """Default fallback when no executor is registered for a definition."""

    def step_precheck(self, ctx: ProbeContext) -> StepResult:
        if getattr(ctx.definition, "enterprise_only", False):
            msg = "Capability-gated: enterprise-only feature not enabled in this workspace."
            rc = RootCauseBucket.FEATURE_NOT_ENABLED
        else:
            msg = (
                f"No executor implemented for '{getattr(ctx.definition, 'executor_key', '?')}'. "
                "Seeded as metadata; register a plugin to enable live execution."
            )
            rc = RootCauseBucket.SERVICE_UNSUPPORTED_IN_REGION
        return StepResult(
            StepKind.PRECHECK,
            ProbeOutcome.SKIP,
            ConnectivityOutcome.UNKNOWN,
            message=msg,
            root_cause_bucket=rc,
            recommendation="Implement and @register an executor for this executor_key.",
        )

    def step_functional(self, ctx: ProbeContext) -> StepResult:
        return StepResult(StepKind.FUNCTIONAL, ProbeOutcome.SKIP, ConnectivityOutcome.UNKNOWN,
                          message="Skipped (capability-gated stub).")


def _make_stub(key: str):
    @register(key)
    class _Stub(CapabilityGatedStub):
        executor_key = key

    _Stub.__name__ = f"Stub_{key}"
    return _Stub


# Register named stubs for catalog executor_keys that have no live plugin yet.
# Implementing a real executor is simply re-registering one of these keys.
_STUB_KEYS = [
    "fivetran_landing",
    "iics_landing",
    "autoloader_smoke",
    "oracle_federation",
    "inbound_generic",
    "all_purpose_compute",
    "job_compute",
    "serverless_compute",
    "github_access",
    "genie_query",
    "agent_enterprise",
    "powerbi_connectivity",
    "tableau_connectivity",
    "salesforce_outbound",
    "tm1_outbound",
    "oracle_outbound",
    "n8n_connectivity",
    "outbound_generic",
]

for _key in _STUB_KEYS:
    _make_stub(_key)
