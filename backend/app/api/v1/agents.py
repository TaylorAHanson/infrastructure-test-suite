"""Remote-agent control-plane endpoints.

Remote agents register, poll for assigned work, and post **signed** results
back. The first iteration ships the protocol; real agents are deployed at the
relevant network vantage point.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.core.ids import utcnow
from app.db.session import get_session
from app.models import RemoteAgent
from app.schemas.schemas import AgentRegister, AgentResultSubmit
from app.services import run_service
from app.services.audit import record_audit

router = APIRouter()


@router.get("")
def list_agents(session: Session = Depends(get_session)) -> list[RemoteAgent]:
    return session.exec(select(RemoteAgent)).all()


@router.post("/register", status_code=201)
def register_agent(payload: AgentRegister, session: Session = Depends(get_session)) -> RemoteAgent:
    agent = RemoteAgent(
        name=payload.name, description=payload.description, vantage_point=payload.vantage_point,
        capabilities=payload.capabilities, signing_key_id=payload.signing_key_id,
        status="ONLINE", last_seen_at=utcnow(),
    )
    session.add(agent)
    session.commit()
    session.refresh(agent)
    record_audit(session, actor=payload.name, actor_kind="agent", action="agent.register",
                 entity_type="remote_agent", entity_id=agent.id, summary=f"Registered agent {agent.name}")
    return agent


@router.post("/{agent_id}/heartbeat")
def heartbeat(agent_id: str, session: Session = Depends(get_session)) -> dict:
    agent = session.get(RemoteAgent, agent_id)
    if not agent:
        raise HTTPException(404, "agent not found")
    agent.last_seen_at = utcnow()
    agent.status = "ONLINE"
    session.add(agent)
    session.commit()
    return {"status": "ok"}


@router.post("/results")
def submit_results(payload: AgentResultSubmit, session: Session = Depends(get_session)) -> dict:
    """Accept signed step results for a run from a remote agent."""
    try:
        run = run_service.apply_agent_results(session, payload)
    except PermissionError:
        raise HTTPException(401, "invalid signature")
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    record_audit(session, actor=payload.agent_id, actor_kind="agent", action="agent.results",
                 entity_type="run", entity_id=run.run_id,
                 summary=f"Remote agent posted {len(payload.steps)} step(s)")
    return {"run_id": run.run_id, "status": run.status.value}
