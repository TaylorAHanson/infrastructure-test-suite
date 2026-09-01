"""Sandbox Functional (issue #97), split from the monolithic real-catalog
module (#107): the catalog's one mutating validation — create a trivial Job,
run it, confirm it produces output, then delete it via cleanup.

Confirmed R2: a create+run+delete lifecycle is a full mutating round-trip
that the coarse verb guess (submit -> POST/R1) would under-classify (ADR-0017
§2c/3) — the confirmation keeps this sandbox-only under the safety-class cap
matrix (ADR-0004). This module contributes no ``ci_gate_tests``: a mutating
Check must never be a CI Gate member.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

CHECK_SPECS: list[tuple[str, dict]] = [
    ("job_smoke", dict(
        display_name="Job create/run/delete smoke",
        description=(
            "Submit a trivial one-time Databricks Job run, confirm it completes "
            "and produces output, then delete the run via the generic handler's "
            "cleanup hook. Confirmed R2: a create+run+delete lifecycle is a full "
            "mutating round-trip that the coarse verb guess (submit -> POST/R1) "
            "would under-classify (ADR-0017 §2c/3) — the confirmation keeps this "
            "sandbox-only under the safety-class cap matrix (ADR-0004)."
        ),
        operation=dict(
            service="jobs", method="submit",
            params={
                "run_name": "infra-test-suite-job-smoke",
                "tasks": [{"task_key": "smoke",
                          "notebook_task": {"notebook_path": "/Shared/job-smoke"}}],
                "demo_evidence": {"run_id": "demo-run-1", "output": "job-smoke ok"},
                "cleanup": {"service": "jobs", "method": "delete_run",
                           "params": {"run_id": "$response.run_id"}},
            }),
        handler_key="databricks_sdk",
        assertion="the job run completes and produces output",
        confirmed_risk_tier="R2")),
]


TESTS: dict[str, TestSpec] = {
    "job_smoke": TestSpec(
        "Job create/run/delete smoke",
        "Create, run, and delete a trivial job — proves the ingestion layer "
        "actually executes, not just reachable (sandbox-only; cleans up after itself).",
        [("job_smoke", "assertion")],
        ["sandbox", "producer", "functional"],
    ),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Sandbox Functional", "Mutating validations confined to sandbox targets, with automatic cleanup.",
             ["job_smoke"], tags=["seeded", "sandbox", "functional"]),
]


CONTRIBUTION = Contribution(
    check_specs=CHECK_SPECS,
    tests=TESTS,
    suites=SUITES,
)
