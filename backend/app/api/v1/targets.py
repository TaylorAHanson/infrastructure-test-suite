"""Target registry endpoints (workspaces under test).

Registration is **explicit opt-in** (a never-registered workspace is untouchable,
user story 1). Every write here is audited (ADR-0014). Two safety-relevant
guards run at write time:

* **Test-connection-before-save** (ADR-0003 §3) — a target with a broken/absent
  identity handle is refused, so a saved target is a proven-reachable one.
* **Role-registration guard** (ADR-0018 §2) — an assumable role whose class
  out-runs the target's safety class is rejected, keeping assumption from
  widening the primary identity bound.

The safety class is **authoritative and never derived from the workspace name**
(user story 4) — it is only ever what the operator explicitly set.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.clients.databricks import probe_identity
from app.core.enums import SafetyClass
from app.db.session import get_session
from app.models import Target
from app.schemas.schemas import TargetCreate, TargetUpdate
from app.services.audit import record_audit
from app.services.roles import RoleRegistrationError, validate_roles_for_class

router = APIRouter()


def _validate_roles(raw_roles, safety_class: SafetyClass) -> list[dict]:
    try:
        return validate_roles_for_class(raw_roles, safety_class)
    except RoleRegistrationError as exc:
        raise HTTPException(422, str(exc))


def _run_test_connection(identity_handle: Optional[str], host: Optional[str]) -> None:
    """Refuse the save if the identity does not pass test-connection."""
    result = probe_identity(identity_handle, host)
    if not result.ok:
        raise HTTPException(422, f"test-connection failed: {result.message}")


@router.get("")
def list_targets(
    session: Session = Depends(get_session),
    q: Optional[str] = None,
    name: Optional[str] = None,
    host: Optional[str] = None,
    safety_class: Optional[SafetyClass] = None,
) -> list[Target]:
    """List/select targets. Selection helpers: filter by name/host/class or a
    free-text ``q`` over name+workspace (user story 11 — always explicit)."""
    rows = session.exec(select(Target)).all()
    if name is not None:
        rows = [t for t in rows if t.name == name or t.workspace_name == name]
    if host is not None:
        rows = [t for t in rows if (t.host or "") == host]
    if safety_class is not None:
        rows = [t for t in rows if t.safety_class == safety_class]
    if q:
        ql = q.lower()
        rows = [t for t in rows
                if ql in t.name.lower() or ql in (t.workspace_name or "").lower()]
    return rows


@router.get("/{target_id}")
def get_target(target_id: str, session: Session = Depends(get_session)) -> Target:
    target = session.get(Target, target_id)
    if not target:
        raise HTTPException(404, "target not found")
    return target


@router.post("", status_code=201)
def create_target(payload: TargetCreate, session: Session = Depends(get_session),
                  actor: str = Query("api-user")) -> dict:
    data = payload.model_dump()
    roles = _validate_roles(data["assumable_roles"], payload.safety_class)
    _run_test_connection(payload.identity_handle, payload.host)
    data["assumable_roles"] = roles  # store the normalized, guarded role dicts
    target = Target(**data)
    session.add(target)
    session.commit()
    session.refresh(target)
    out = target.model_dump(mode="json")
    record_audit(session, actor=actor, action="target.register", entity_type="target", entity_id=target.id,
                 summary=f"Registered target {out['name']} (class={out['safety_class']})", after=out)
    if roles:
        record_audit(session, actor=actor, action="target.roles.register", entity_type="target",
                     entity_id=target.id, summary=f"Registered {len(roles)} assumable role(s)",
                     after={"assumable_roles": roles})
    return out


@router.post("/{target_id}/test-connection")
def test_connection(target_id: str, session: Session = Depends(get_session)) -> dict:
    """Probe a saved target's identity without changing it (ADR-0003 §3)."""
    target = session.get(Target, target_id)
    if not target:
        raise HTTPException(404, "target not found")
    result = probe_identity(target.identity_handle, target.host)
    return {"ok": result.ok, "message": result.message}


@router.post("/test-connection")
def test_connection_adhoc(payload: TargetCreate) -> dict:
    """Probe a candidate identity before the target is saved (add-target flow)."""
    result = probe_identity(payload.identity_handle, payload.host)
    return {"ok": result.ok, "message": result.message}


@router.patch("/{target_id}")
def update_target(target_id: str, payload: TargetUpdate, session: Session = Depends(get_session),
                  actor: str = Query("api-user")) -> dict:
    target = session.get(Target, target_id)
    if not target:
        raise HTTPException(404, "target not found")
    data = payload.model_dump(exclude_unset=True)
    before = target.model_dump(mode="json")

    new_class = data.get("safety_class", target.safety_class)
    if isinstance(new_class, str):
        new_class = SafetyClass(new_class)

    # Re-validate roles against the (possibly new) class whenever either changes.
    roles_changed = "assumable_roles" in data or "safety_class" in data
    if roles_changed:
        raw_roles = data.get("assumable_roles", target.assumable_roles)
        data["assumable_roles"] = _validate_roles(raw_roles, new_class)

    # Re-run test-connection if the identity (or host) changed.
    if "identity_handle" in data or "host" in data:
        _run_test_connection(data.get("identity_handle", target.identity_handle),
                             data.get("host", target.host))

    class_changed = "safety_class" in data and new_class != target.safety_class
    downgrade = class_changed and new_class.max_risk_tier.order > target.safety_class.max_risk_tier.order

    for key, value in data.items():
        setattr(target, key, value)
    session.add(target)
    session.commit()
    session.refresh(target)
    out = target.model_dump(mode="json")

    if class_changed:
        # Safety-class change is the standing forensic control (ADR-0014);
        # a downgrade (wider blast radius) is flagged explicitly.
        record_audit(session, actor=actor, action="target.safety_class.change", entity_type="target",
                     entity_id=target.id,
                     summary=f"Safety class {before['safety_class']} → {out['safety_class']}"
                             + (" (DOWNGRADE — wider blast radius)" if downgrade else ""),
                     before={"safety_class": before["safety_class"], "downgrade": downgrade},
                     after={"safety_class": out["safety_class"]})
    if "assumable_roles" in data:
        record_audit(session, actor=actor, action="target.roles.change", entity_type="target",
                     entity_id=target.id, summary="Assumable roles updated",
                     before={"assumable_roles": before["assumable_roles"]},
                     after={"assumable_roles": out["assumable_roles"]})
    record_audit(session, actor=actor, action="target.edit", entity_type="target", entity_id=target.id,
                 summary=f"Edited target {out['name']}", before=before, after=out)
    return out


@router.delete("/{target_id}")
def deregister_target(target_id: str, session: Session = Depends(get_session),
                      actor: str = Query("api-user")) -> dict:
    """Deregister a target: a deregistered workspace is untouchable again
    (user story 1). A **soft** deregister (``enabled=False``) — the row and its
    run history survive, and the deregistration itself is an audited config-plane
    mutation (ADR-0014). Idempotent: deregistering an already-off target is a no-op
    write but still records the intent."""
    target = session.get(Target, target_id)
    if not target:
        raise HTTPException(404, "target not found")
    before = target.model_dump(mode="json")
    target.enabled = False
    session.add(target)
    session.commit()
    session.refresh(target)
    out = target.model_dump(mode="json")
    record_audit(session, actor=actor, action="target.deregister", entity_type="target",
                 entity_id=target.id, summary=f"Deregistered target {out['name']}",
                 before=before, after=out)
    return out
