"""The Assistant endpoints (ADR-0012).

The shelf's page-aware turn answers questions about the catalog, suggests a
cause for a finding (RCA), or hands a request to write a Test to the test
editor, whose own agent (``/editor-turn``) proposes scripts. Plus the
assistant's model settings. Guardrails ride on the services, not here — this
layer is thin HTTP glue.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.api.deps import Actor, current_actor
from app.db.session import get_session
from app.schemas.schemas import (
    AssistantConfigOut,
    AssistantConfigUpdate,
    AssistantTurnIn,
    EditorTurnIn,
    ModelTestIn,
    RcaRequestIn,
)
from app.services import assistant, rca
from app.services.assistant import get_model_call

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/turn")
def assistant_turn(payload: AssistantTurnIn,
                   session: Session = Depends(get_session),
                   model_call=Depends(get_model_call)) -> dict:
    """Route one turn to a catalog answer, an editor handoff or RCA, grounded in the page context."""
    return assistant.run_turn(session, payload, model_call=model_call)


def get_editor_model():
    """The editor agent's model seam; overridden in tests."""
    return None


_NO_MODEL = {"reply": "The assistant needs a model. Pick one in Settings → Assistant, or set "
                      "MODEL_SERVING_ENDPOINT (the app's serving-endpoint resource).",
             "proposal": None, "steps": [], "dry_run": None, "error": True}


def _turn_kwargs(payload: EditorTurnIn) -> dict:
    return {"messages": [m.model_dump() for m in payload.messages], "buffer": payload.script,
            "test_id": payload.test_id, "target_id": payload.target_id, "last_dry_run": payload.last_dry_run}


@router.post("/editor-turn")
def editor_turn(payload: EditorTurnIn, session: Session = Depends(get_session),
                model_call=Depends(get_editor_model)) -> dict:
    """One turn of the test-editor agent. It may propose a script; it never saves one."""
    from app.services import editor_agent

    if model_call is None and not editor_agent.model_available():
        return _NO_MODEL
    return editor_agent.editor_turn(session, model_call=model_call, **_turn_kwargs(payload))


@router.post("/editor-turn/stream")
async def editor_turn_stream(payload: EditorTurnIn, model_call=Depends(get_editor_model)) -> StreamingResponse:
    """The same turn as ``/editor-turn``, streamed as server-sent events.

    Each ``data:`` line is one JSON event — ``thinking``, ``message``,
    ``reasoning``, ``tool_start``, ``tool_end``, ``proposal``, ``notice`` — and the
    last is ``done``, carrying exactly what ``/editor-turn`` returns. Closing the
    connection stops the turn at its next step."""
    from app.db.session import session_scope
    from app.services import editor_agent

    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()
    cancel = threading.Event()

    def push(event: Optional[dict]) -> None:
        try:
            loop.call_soon_threadsafe(events.put_nowait, event)
        except RuntimeError:  # the loop is gone: the client left
            cancel.set()

    def work() -> None:
        try:
            if model_call is None and not editor_agent.model_available():
                push({"type": "done", **_NO_MODEL})
                return
            with session_scope() as session:
                out = editor_agent.editor_turn(session, model_call=model_call, emit=push, cancel=cancel,
                                               **_turn_kwargs(payload))
            push({"type": "done", **out})
        except Exception as exc:  # noqa: BLE001
            logger.exception("editor turn failed")
            push({"type": "done", "reply": f"The assistant failed: {exc}", "proposal": None, "steps": [],
                  "dry_run": None, "error": True})
        finally:
            push(None)

    threading.Thread(target=work, name="editor-turn", daemon=True).start()

    async def stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(events.get(), timeout=10)
                except TimeoutError:
                    # Keeps proxies from closing a connection that's waiting on a slow model call.
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    return
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            cancel.set()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _config_out(session: Session) -> AssistantConfigOut:
    from app.services import assistant_config

    row = assistant_config.get_config(session)
    eff = assistant_config.effective(session)
    limits = {k: [lo, hi] for k, (lo, hi) in assistant_config.INT_LIMITS.items()}
    limits["temperature"] = list(assistant_config.TEMPERATURE_RANGE)
    return AssistantConfigOut(**{f: getattr(row, f) for f in assistant_config.FIELDS},
                              default_endpoint=eff.default_endpoint, model=eff.model,
                              updated_by=row.updated_by, updated_at=row.updated_at, limits=limits)


@router.get("/config", response_model=AssistantConfigOut)
def get_assistant_config(session: Session = Depends(get_session)) -> AssistantConfigOut:
    """Which model the assistant uses and how far one turn may go."""
    return _config_out(session)


@router.put("/config", response_model=AssistantConfigOut)
def update_assistant_config(payload: AssistantConfigUpdate, session: Session = Depends(get_session),
                            actor: Actor = Depends(current_actor)) -> AssistantConfigOut:
    """Change the assistant's settings; audited as ``assistant.config.change``."""
    from app.services import assistant_config

    try:
        assistant_config.update_config(session, actor=actor.handle, actor_kind=actor.kind,
                                       **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _config_out(session)


@router.get("/models")
def list_assistant_models(refresh: bool = False) -> dict:
    """Chat models the assistant can use here, foundation (``system.ai``) models first."""
    from app.services import assistant_config

    return assistant_config.list_models(refresh=refresh)


@router.post("/models/test")
def test_assistant_model(payload: ModelTestIn) -> dict:
    """Send one tiny tool-calling request to a model, as the assistant would."""
    from app.services import assistant_config

    endpoint = payload.endpoint or assistant_config.effective().model
    if not endpoint:
        raise HTTPException(status_code=422, detail="no model chosen")
    try:
        return assistant_config.test_model(endpoint, payload.reasoning)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/rca")
def rca_suggest(payload: RcaRequestIn,
                session: Session = Depends(get_session),
                model_call=Depends(get_model_call)) -> dict:
    """Suggest (never assert) a cause + lane for a finding — read-only, advisory."""
    return rca.suggest(session, check_id=payload.check_id, target_id=payload.target_id,
                       turn=payload.turn, model_call=model_call).to_dict()
