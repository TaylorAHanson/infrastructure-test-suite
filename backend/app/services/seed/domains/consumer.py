"""Consumer: secrets + model-serving posture (issue #96), split from the
monolithic real-catalog module (#107).

Same shape as the tutorial domain's ``CHECK_SPECS`` — see
:mod:`app.services.seed.domains.producer` for the pattern this and its
sibling posture modules share. Both entries are reachability-only reads: a
clean workspace may have zero secret scopes or serving endpoints, so neither
asserts non-emptiness.

"Consumer Suite" below names ``sql``, ``uc``, and ``egress`` — Test keys the
tutorial domain module owns — alongside this module's own
``consumer_posture``: a cross-module Suite membership the registry resolves
once every domain's Checks/Tests are merged (#106).
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

CHECK_SPECS: list[tuple[str, dict]] = [
    ("secret_scopes_exist", dict(
        display_name="Secret scopes exist (credentials aren't hardcoded)",
        description="List Databricks secret scopes to confirm credentials are stored in scopes, "
                    "not hardcoded. A clean workspace may have zero scopes — reachability only, "
                    "no false-fail.",
        operation=dict(service="secrets", method="list_scopes", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the secret scope list without error")),
    ("model_serving_enumerates", dict(
        display_name="Model-serving layer enumerates without error",
        description="List Model Serving endpoints to confirm the serving layer is reachable. A "
                    "clean workspace may have zero endpoints — reachability only, no false-fail.",
        operation=dict(service="serving_endpoints", method="list", verb="GET", params={}),
        handler_key="databricks_sdk",
        assertion="the call returns the model-serving endpoint list without error")),
]


TESTS: dict[str, TestSpec] = {
    "consumer_posture": TestSpec(
        "Consumer security posture",
        "Secret scopes exist (no hardcoded credentials) and the model-serving "
        "layer enumerates without error.",
        [("secret_scopes_exist", "assertion"),
         ("model_serving_enumerates", "assertion")],
        ["consumer", "security", "posture"],
    ),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Consumer Suite", "SQL access, UC governance, and egress paths.",
             ["sql", "uc", "egress", "consumer_posture"], tags=["seeded", "consumer"]),
]


CONTRIBUTION = Contribution(
    check_specs=CHECK_SPECS,
    tests=TESTS,
    suites=SUITES,
)
