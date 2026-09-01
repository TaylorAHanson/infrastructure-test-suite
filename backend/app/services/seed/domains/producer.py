"""Producer: ingestion-layer posture (issue #96), split from the monolithic
real-catalog module (#107).

Same shape as the tutorial domain's ``CHECK_SPECS``: (key, CheckCreate
kwargs), seeded through the identical ``build_check_fields()`` path
(:func:`app.services.seed.build.make_check`) — no ``TUTORIAL_TAGS``, since
this is headline catalog, not a worked example. The read-only entry declares
``verb="GET"`` explicitly (R0) and phrases its assertion as bare success
("... without error"), the reachability shape for a resource that can
legitimately be empty on a clean workspace (a fresh install has zero DLT
pipelines); asserting non-emptiness there would false-fail.

"Producer Suite" below names ``producer`` — a Test key the tutorial domain
module owns — alongside this module's own ``producer_posture``: a
cross-module Suite membership the registry resolves once every domain's
Checks/Tests are merged (#106).
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

CHECK_SPECS: list[tuple[str, dict]] = [
    ("dlt_pipeline_target_declared", dict(
        display_name="DLT pipeline declares a target catalog/schema",
        description="List Delta Live Tables pipelines to confirm the ingestion layer's pipeline "
                    "definitions (which declare a target catalog/schema) are reachable. A clean "
                    "workspace may have zero pipelines — reachability only, no false-fail.",
        operation=dict(service="pipelines", method="list_pipelines", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the pipeline list without error")),
]


TESTS: dict[str, TestSpec] = {
    "producer_posture": TestSpec(
        "Producer ingestion posture",
        "DLT pipelines are reachable and declare a target catalog/schema.",
        [("dlt_pipeline_target_declared", "assertion")],
        ["producer", "ingestion", "posture"],
    ),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Producer Suite", "Ingestion and pipeline layer reachability.",
             ["producer", "producer_posture"], tags=["seeded", "producer"]),
]


CONTRIBUTION = Contribution(
    check_specs=CHECK_SPECS,
    tests=TESTS,
    suites=SUITES,
)
