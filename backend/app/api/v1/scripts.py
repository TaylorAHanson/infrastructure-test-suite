"""Script-editor endpoints: analysis, dry runs, and the Databricks SDK reference."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import Actor, current_actor
from app.core.enums import RequestSource
from app.db.session import get_session
from app.models import Target
from app.schemas.schemas import ScriptAnalyze, ScriptDryRun
from app.script_runtime import codegen, sdk_catalog
from app.script_runtime.analysis import analyze
from app.services import script_tests
from app.services.safety import SafetyOverride

router = APIRouter()


@router.get("/template")
def template(name: str = "", description: str = "") -> dict:
    return {"script": codegen.new_test_script(name, description)}


@router.post("/analyze")
def analyze_script(payload: ScriptAnalyze) -> dict:
    return analyze(payload.script)


@router.post("/dry-run")
def dry_run(payload: ScriptDryRun, session: Session = Depends(get_session),
            actor: Actor = Depends(current_actor)) -> dict:
    """Run a script once against a target. Gated like a real run; nothing is recorded."""
    target = session.get(Target, payload.target_id)
    if target is None:
        raise HTTPException(404, "target not found")
    source = RequestSource.UI_MANUAL if actor.is_human else RequestSource.API
    try:
        return script_tests.dry_run(
            session, script=payload.script, target=target, depth=payload.depth, actor=actor.handle,
            request_source=source, run_as_role=payload.run_as_role, params=payload.params,
            override=SafetyOverride.from_payload(payload.override.model_dump() if payload.override else None))
    except script_tests.TargetBusy as exc:
        raise HTTPException(409, str(exc))


@router.get("/sdk/index")
def sdk_index() -> dict:
    """Every ``w.<service>.<method>`` with its verb — powers editor autocomplete."""
    return {name: {m.method: {"verb": sdk_catalog.verb_for(name, m.method), "summary": m.summary,
                              "signature": m.signature}
                   for m in svc.methods.values()}
            for name, svc in sdk_catalog.catalog().items()}


@router.get("/sdk/search")
def sdk_search(q: str = Query(..., min_length=1), limit: int = 20) -> list[dict]:
    return sdk_catalog.search(q, limit=limit)


@router.get("/sdk/{service}")
def sdk_describe(service: str, method: str | None = None) -> dict:
    out = sdk_catalog.describe(service, method)
    if "error" in out:
        raise HTTPException(404, out)
    return out
