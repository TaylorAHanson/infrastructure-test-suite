"""AI-assisted test authoring endpoints.

``POST /authoring/generate`` drafts a runnable test spec from a natural-language
description. The draft is NOT saved - the UI lets the user review/edit and then
save via ``POST /tests`` (normal validation + audit).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.clients.model_serving import ModelServingError, default_endpoint
from app.services import authoring

router = APIRouter()


class GenerateRequest(BaseModel):
    description: str
    category: str | None = None


class GenerateResponse(BaseModel):
    spec: dict
    endpoint: str | None = None


@router.get("/capabilities")
def capabilities() -> dict:
    """Expose what the authoring tool can target + whether AI is configured."""
    return {
        "model_serving_endpoint": default_endpoint(),
        "ai_available": bool(default_endpoint()),
        "executors": [
            {"executor_key": k, "kind": v["kind"], "purpose": v["purpose"], "params": v["params"]}
            for k, v in authoring.EXECUTOR_GUIDE.items()
        ],
    }


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    from fastapi import HTTPException

    if not req.description.strip():
        raise HTTPException(422, "description is required")
    try:
        spec = authoring.generate_test_spec(req.description, req.category)
    except ModelServingError as exc:
        raise HTTPException(503, f"AI model unavailable: {exc}")
    except ValueError as exc:
        raise HTTPException(422, f"Could not generate a valid test: {exc}")
    return GenerateResponse(spec=spec, endpoint=default_endpoint())
