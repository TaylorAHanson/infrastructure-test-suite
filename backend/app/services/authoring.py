"""AI-assisted test authoring.

Turns a natural-language description ("check our serverless warehouse can run a
query", "verify egress to api.stripe.com") into a concrete, runnable
:class:`TestDefinitionCreate` spec. The model is constrained to the real,
registered executors and our enums so whatever it produces is immediately
runnable - not a free-form blob.

The model only *drafts*; the user reviews/edits in the UI and an explicit save
goes through the normal ``POST /tests`` validation. This keeps a human in the
loop and never auto-creates tests from raw model output.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.clients.model_serving import ModelServingError, chat
from app.core.enums import ExecutorKind, TestCategory
from app.executors.registry import registry
from app.schemas.schemas import TestDefinitionCreate

logger = logging.getLogger(__name__)


# Real, registered executors the model is allowed to target, with the params
# each one understands. Keep this in sync with the builtin executors.
EXECUTOR_GUIDE: dict[str, dict] = {
    "sql_warehouse_smoke": {
        "kind": ExecutorKind.SQL_WAREHOUSE.value,
        "purpose": "Run a deterministic SQL query on a SQL warehouse and verify the result.",
        "params": {"query": "SQL string, e.g. 'SELECT 1'", "warehouse_id": "optional; defaults to bound warehouse"},
        "category": TestCategory.PROCESSING.value,
    },
    "unity_catalog_query": {
        "kind": ExecutorKind.SQL_WAREHOUSE.value,
        "purpose": "Validate Unity Catalog access by listing catalogs/schemas or counting rows in a table.",
        "params": {"statement": "SQL, e.g. 'SHOW CATALOGS' or 'SELECT count(*) FROM cat.sch.tbl'",
                   "catalog": "optional", "schema": "optional"},
        "category": TestCategory.PROCESSING.value,
    },
    "lakebase_smoke": {
        "kind": ExecutorKind.APP_INLINE.value,
        "purpose": "Verify the app's Lakebase (Postgres) connection with a trivial transaction.",
        "params": {},
        "category": TestCategory.PROCESSING.value,
    },
    "model_serving_invoke": {
        "kind": ExecutorKind.MODEL_SERVING.value,
        "purpose": "Invoke a Model Serving endpoint (Foundation Model) and verify a response.",
        "params": {"endpoint": "optional endpoint name; defaults to the configured one",
                   "prompt": "optional prompt string"},
        "category": TestCategory.PROCESSING.value,
    },
    "databricks_app_health": {
        "kind": ExecutorKind.APP_INLINE.value,
        "purpose": "Verify the app's own OAuth identity and workspace reachability via the SDK.",
        "params": {},
        "category": TestCategory.PROCESSING.value,
    },
    "outbound_http": {
        "kind": ExecutorKind.APP_INLINE.value,
        "purpose": "Check outbound/egress connectivity to an external HTTP(S) endpoint (DNS+TLS+request).",
        "params": {"url": "https URL", "method": "GET/POST/HEAD (default GET)",
                   "expected_status": "int (default 200)",
                   "connection_name": "optional UC HTTP connection name (preferred over url)"},
        "category": TestCategory.CONSUMER.value,
    },
}


def _system_prompt() -> str:
    guide_lines = []
    for key, meta in EXECUTOR_GUIDE.items():
        params = ", ".join(f"{k} ({v})" for k, v in meta["params"].items()) or "none"
        guide_lines.append(
            f'- executor_key "{key}" [kind={meta["kind"]}, default category={meta["category"]}]: '
            f'{meta["purpose"]} Parameters: {params}.'
        )
    guide = "\n".join(guide_lines)
    return (
        "You design regression/connectivity test definitions for a Databricks "
        "validation platform. Given a user's request, choose the single best "
        "executor and produce ONE test definition as STRICT JSON (no markdown, "
        "no prose).\n\n"
        "Available executors (you MUST pick one of these executor_key values):\n"
        f"{guide}\n\n"
        "Output JSON with EXACTLY these fields:\n"
        '{\n'
        '  "display_name": string,\n'
        '  "description": string,\n'
        '  "category": one of "PRODUCER" | "PROCESSING" | "CONSUMER",\n'
        '  "tags": string[],\n'
        '  "target_type": short string like "warehouse" | "catalog" | "database" | "endpoint" | "external_system" | "workspace" | "app",\n'
        '  "executor_kind": one of "SQL_WAREHOUSE" | "APP_INLINE" | "MODEL_SERVING" | "DATABRICKS_JOB" | "REMOTE_AGENT",\n'
        '  "executor_key": one of the keys listed above,\n'
        '  "supported_execution_modes": subset of ["PREFLIGHT","SMOKE","FULL"],\n'
        '  "destructive": boolean,\n'
        '  "default_parameters": object matching the chosen executor\'s parameters\n'
        "}\n\n"
        "Rules: executor_kind must match the chosen executor_key's kind. Prefer "
        "non-destructive read-only checks. For external connectivity always use "
        "outbound_http. Return ONLY the JSON object."
    )


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of the model reply (tolerates ```json fences)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # strip leading ```json / ``` and trailing ```
        cleaned = cleaned.split("```", 2)[1] if cleaned.count("```") >= 2 else cleaned.strip("`")
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model response.")
    return json.loads(cleaned[start : end + 1])


def generate_test_spec(description: str, category: Optional[str] = None) -> dict:
    """Generate a validated, runnable TestDefinitionCreate-compatible dict.

    Raises :class:`ModelServingError` if the model is unavailable, or
    ``ValueError`` if the response can't be coerced into a valid spec.
    """
    user_msg = description.strip()
    if category:
        user_msg += f"\n\n(Hint: the user expects category {category}.)"

    reply = chat(
        messages=[
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=900,
        temperature=0.1,
    )
    raw = _extract_json(reply)

    # Clamp the executor_key to a real one; default to outbound_http only if it
    # looks like a connectivity ask, else sql_warehouse_smoke.
    key = raw.get("executor_key")
    if key not in registry or key not in EXECUTOR_GUIDE:
        logger.info("Model picked unknown executor_key %r; clamping.", key)
        key = "sql_warehouse_smoke"
    meta = EXECUTOR_GUIDE[key]
    raw["executor_key"] = key
    raw["executor_kind"] = meta["kind"]
    raw.setdefault("category", meta["category"])
    raw.setdefault("supported_execution_modes", ["PREFLIGHT", "SMOKE"])
    raw.setdefault("tags", [])
    raw.setdefault("target_type", "workspace")
    raw.setdefault("default_parameters", {})
    raw.setdefault("description", description.strip())

    # Validate by constructing the create schema (raises on bad enums/shape),
    # then return the normalized dict the UI can edit and POST back.
    spec = TestDefinitionCreate(**{
        "display_name": raw.get("display_name") or description.strip()[:80],
        "description": raw.get("description", ""),
        "category": raw["category"],
        "tags": raw.get("tags", []),
        "target_type": raw.get("target_type", "workspace"),
        "required_capabilities": [key],
        "supported_execution_modes": raw.get("supported_execution_modes", ["PREFLIGHT", "SMOKE"]),
        "executor_kind": raw["executor_kind"],
        "executor_key": key,
        "destructive": bool(raw.get("destructive", False)),
        "default_parameters": raw.get("default_parameters", {}),
    })
    return spec.model_dump(mode="json")
