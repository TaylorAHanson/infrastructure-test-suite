"""Python-script Tests: save, run (one script run → one CheckRun per check), dry-run.

Each ``with check("name")`` block is a Check row (``handler_key="script"``,
keyed stably by ``(test, name)``) and a member of the Test, so run history,
last-good, findings, flaky detection and version pins hang off it. Saving a
script re-syncs those rows; running a Test runs its script once per target and
writes each check's verdict onto its CheckRun.
"""
from __future__ import annotations

import logging
import threading
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, Optional

from sqlmodel import Session, select

from app.core.enums import (
    Depth,
    Evaluator,
    Expectation,
    RequestSource,
    RiskTier,
    RunStatus,
    StageStatus,
    TestStatus,
)
from app.core.ids import utcnow
from app.models import Check, CheckRun, RunRequest, StageOutcome, Target, Test
from app.schemas.schemas import TestCheckIn
from app.script_runtime import codegen
from app.script_runtime.analysis import analyze
from app.script_runtime.runner import CheckResult, RunContext, ScriptResult, run_script
from app.services.audit import record_audit, version_pointer

logger = logging.getLogger(__name__)

SCRIPT_HANDLER = "script"
_EDITABLE = ("name", "description", "tags", "run_as_role")
_dynamic_lock = threading.Lock()


class ScriptInvalid(ValueError):
    """The script can't be saved: syntax error, unknown SDK call, or a blocklisted call."""

    def __init__(self, analysis: dict):
        self.analysis = analysis
        super().__init__("; ".join(f"line {e.get('line')}: {e['message']}" if e.get("line") else e["message"]
                                   for e in analysis.get("errors", [])) or "invalid script")


class TargetBusy(RuntimeError):
    """A mutating target's lease is held by a run; a dry run would collide with it."""


def is_script_check(check: Optional[Check]) -> bool:
    return check is not None and check.handler_key == SCRIPT_HANDLER


def script_check_name(check: Check) -> str:
    return (check.hints or {}).get("script_check_name") or check.display_name


def script_for(test: Test) -> str:
    """The Test's script; one that has none yet opens as the new-Test template."""
    return test.script or codegen.new_test_script(test.name, test.description)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------
def _tier_of(calls: list[dict]) -> str:
    order = {t.value: t.order for t in RiskTier}
    best = RiskTier.R0.value
    for c in calls:
        tier = c.get("tier")
        if tier in order and order[tier] > order[best]:
            best = tier
    return best


def _calls_by_check(analysis: dict, script: str) -> dict[str, list[dict]]:
    """Attribute each statically-found call to the check whose block contains it."""
    import ast

    spans: list[tuple[int, int, str]] = []
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return {}
    by_line = {c["line"]: c["name"] for c in analysis["checks"]}
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                name = by_line.get(getattr(item.context_expr, "lineno", -1))
                if name:
                    spans.append((node.lineno, node.end_lineno or node.lineno, name))
    out: dict[str, list[dict]] = {}
    for call in analysis["calls"]:
        for start, end, name in spans:
            if start <= (call.get("line") or 0) <= end:
                out.setdefault(name, []).append(call)
                break
    return out


def _check_fields(test: Test, decl: dict, calls: list[dict]) -> dict:
    tier = _tier_of(calls)
    counterfactual = decl["kind"] == "counterfactual"
    return {
        "display_name": decl["name"],
        "description": decl.get("description") or "",
        "operation": {"service": "script", "method": "check",
                      "verb": "GET" if tier == RiskTier.R0.value else "POST", "params": {}},
        "handler_key": SCRIPT_HANDLER,
        "assertion": decl.get("description") or decl["name"],
        "expectation": Expectation.DENY if counterfactual else Expectation.PASS,
        "evaluator": Evaluator.DETERMINISTIC,
        "compiled_predicate": {},
        "compilable": True,
        "expected_stage": decl.get("expected_stage") if counterfactual else None,
        "risk_tier": tier,
        "hints": {"script_test_id": test.id, "script_check_name": decl["name"],
                  "advisory": bool(decl.get("advisory")), "role": decl.get("role"),
                  "calls": sorted({c["op"] for c in calls})},
        "enabled": True,
    }


def _script_checks(session: Session, test_id: str) -> dict[str, Check]:
    rows = session.exec(select(Check).where(Check.handler_key == SCRIPT_HANDLER)).all()
    return {script_check_name(c): c for c in rows if (c.hints or {}).get("script_test_id") == test_id}


def _comparable(value: Any) -> Any:
    return getattr(value, "value", value)


def sync_script_checks(session: Session, test: Test, script: str, analysis: dict) -> None:
    """Make the Test's Check rows + membership match the script's declared checks."""
    from app.services.pinned_versions import set_members

    existing = _script_checks(session, test.id)
    calls = _calls_by_check(analysis, script)
    members: list[TestCheckIn] = []
    for decl in analysis["checks"]:
        fields = _check_fields(test, decl, calls.get(decl["name"], []))
        check = existing.get(decl["name"])
        if check is None:
            check = Check(**fields)
            session.add(check)
            session.flush()
        elif any(_comparable(getattr(check, k)) != _comparable(v) for k, v in fields.items()):
            for k, v in fields.items():
                setattr(check, k, v)
            check.version += 1
            check.updated_at = utcnow()
            session.add(check)
        members.append(TestCheckIn(check_id=check.id, kind=decl["kind"], run_as_role=decl.get("role")))
    set_members(session, test.id, members)


def validate_or_raise(script: str) -> dict:
    analysis = analyze(script)
    if not analysis["ok"]:
        raise ScriptInvalid(analysis)
    return analysis


def save_script(session: Session, test: Test, script: str, actor: str, **fields: Any) -> tuple[Test, dict]:
    """Save ``script`` (and optional Test fields) as the Test's next version."""
    from app.services.pinned_versions import _ensure_test_recorded, record_test_version

    analysis = validate_or_raise(script)
    _ensure_test_recorded(session, test)
    before_version = test.version
    for key in _EDITABLE:
        if key in fields and fields[key] is not None:
            setattr(test, key, fields[key])
    test.script = script
    sync_script_checks(session, test, script, analysis)
    test.version += 1
    test.updated_at = utcnow()
    session.add(test)
    session.commit()
    session.refresh(test)
    record_test_version(session, test, actor)
    record_audit(session, actor=actor, action="test.update", entity_type="test", entity_id=test.id,
                 summary=f"Edited test {test.name} (v{before_version} → v{test.version})",
                 before=version_pointer("test", test.id, before_version),
                 after=version_pointer("test", test.id, test.version))
    return test, analysis


def create_script_test(session: Session, *, name: str, actor: str, script: Optional[str] = None,
                       description: str = "", tags: Optional[list[str]] = None,
                       run_as_role: Optional[str] = None,
                       status: TestStatus = TestStatus.DRAFT) -> tuple[Test, dict]:
    from app.services.pinned_versions import record_test_version

    script = script if script is not None else codegen.new_test_script(name, description)
    analysis = validate_or_raise(script)
    test = Test(name=name, description=description, tags=list(tags or []), run_as_role=run_as_role,
                status=status, origin="human", script=script)
    session.add(test)
    session.flush()
    sync_script_checks(session, test, script, analysis)
    session.commit()
    session.refresh(test)
    record_test_version(session, test, actor)
    record_audit(session, actor=actor, action="test.create", entity_type="test", entity_id=test.id,
                 summary=f"Created test {test.name}", after=version_pointer("test", test.id, test.version))
    return test, analysis


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
def _target_view(target: Optional[Target]) -> Optional[SimpleNamespace]:
    """A detached copy of the Target so the script runs without a session open."""
    if target is None:
        return None
    return SimpleNamespace(
        id=target.id, name=target.name, workspace_name=target.workspace_name, host=target.host,
        safety_class=target.safety_class, tags=dict(target.tags or {}),
        assumable_roles=list(target.assumable_roles or []), identity_handle=target.identity_handle)


def _sdk_timeouts() -> dict:
    from app.core.config import settings

    return {"http_timeout_seconds": settings.SCRIPT_SDK_HTTP_TIMEOUT_SECONDS,
            "retry_timeout_seconds": settings.SCRIPT_SDK_RETRY_TIMEOUT_SECONDS}


def client_factory(target: Optional[SimpleNamespace]):
    """``role -> WorkspaceClient`` as the target's SP (or an assumed role).

    This never falls back to the app's own identity when the target has an
    identity handle that fails to mint: a
    script result must be about the target's identity, not the app's. A target
    with no identity handle runs as the developer's CLI profile only in local
    development; in a deployed app that would test the app's own workspace as
    the app, so it is an identity error instead. Every client's calls are
    bounded by the ``SCRIPT_SDK_*_TIMEOUT_SECONDS`` settings.
    """
    def client_for(role: Optional[str]):
        from app.clients.databricks import IdentityError
        from app.core.config import get_workspace_client, settings
        from app.services.run_service import _resolve_identity_token

        token, _group = _resolve_identity_token(target, role)  # type: ignore[arg-type]
        if token:
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.config import Config

            return WorkspaceClient(config=Config(host=target.host if target else None, token=token,
                                                 auth_type="pat", **_sdk_timeouts()))
        if target is not None and target.identity_handle:
            raise IdentityError(f"could not get a token for target '{target.name}'"
                                + (f" as role '{role}'" if role else ""))
        if target is not None and settings.is_databricks_app:
            raise IdentityError(f"target '{target.name}' has no identity handle, so there is no identity "
                                "to run as: register its service principal (scope/key) on the target")
        return get_workspace_client(**_sdk_timeouts())

    return client_for


def _context(target: Optional[SimpleNamespace], *, depth: Depth, request_source: RequestSource,
             override: Any, default_role: Optional[str], params: Optional[dict] = None) -> RunContext:
    from app.core.config import settings
    from app.services.run_service import _demo_mode

    return RunContext(target=target, depth=depth, request_source=request_source, override=override,
                      demo_mode=_demo_mode(), client_for=client_factory(target), params=params or {},
                      default_role=default_role,
                      timeout_seconds=getattr(settings, "SCRIPT_TIMEOUT_SECONDS", 600),
                      memory_mb=getattr(settings, "SCRIPT_MEMORY_MB", 1024))


_SPINE = ("AUTHENTICATE", "AUTHORIZE", "OPERATE", "VERIFY")
_NETWORK = ("RESOLVE", "CONNECT", "TLS")
_DENIAL_BUCKET = {"AUTHORIZE": "PERMISSION_DENIED", "AUTHENTICATE": "AUTHENTICATION_FAILURE",
                  "RESOLVE": "DNS_RESOLUTION_FAILURE", "CONNECT": "NETWORK_POLICY_DENY",
                  "TLS": "TLS_HANDSHAKE_FAILURE", "OPERATE": "OPERATION_FAILED"}


def _stages(res: CheckResult) -> list[tuple[str, StageStatus, str, Optional[str], dict]]:
    """Map one check's result onto the canonical stage spine.

    The calls themselves live once, on the CheckRun's evidence.
    """
    calls = res.calls
    ops = ", ".join(dict.fromkeys(c["op"] for c in calls[:8])) or "no platform calls"
    op_evidence: dict = {}
    if res.outcome == "pass" and res.kind != "counterfactual":
        return [("AUTHENTICATE", StageStatus.PASS, "Authenticated as the target identity.", None, {}),
                ("AUTHORIZE", StageStatus.PASS, "Every call was authorized.", None, {}),
                ("OPERATE", StageStatus.PASS, f"{len(calls)} call(s): {ops}", None, op_evidence),
                ("VERIFY", StageStatus.PASS, "Every assertion held.", None, dict(res.evidence))]
    if res.outcome == "skipped":
        return [("OPERATE", StageStatus.SKIP, res.message, None, op_evidence)]
    stage = res.stage or "OPERATE"
    if res.outcome == "pass":  # a counterfactual denied where expected
        status, bucket, message = StageStatus.FAIL, _DENIAL_BUCKET.get(stage, "PERMISSION_DENIED"), res.message
    else:
        status = StageStatus.ERROR if res.outcome == "error" else StageStatus.FAIL
        bucket, message = res.bucket or "UNKNOWN", res.message
    if stage in _NETWORK:
        return [(stage, status, message, bucket, op_evidence)]
    out = []
    for s in _SPINE:
        if s == stage:
            out.append((s, status, message, bucket, op_evidence if s == "OPERATE" else dict(res.evidence)))
            break
        out.append((s, StageStatus.PASS, "", None, {}))
    return out


def _verdict(res: CheckResult) -> RunStatus:
    return {"pass": RunStatus.PASS, "fail": RunStatus.FAIL, "error": RunStatus.ERROR,
            "blocked": RunStatus.BLOCKED_BY_SAFETY, "skipped": RunStatus.SKIPPED}.get(res.outcome, RunStatus.ERROR)


def _unreached(name: str, result: ScriptResult) -> CheckResult:
    err = result.error or {}
    kind = err.get("kind")
    res = CheckResult(name=name)
    where = f" (line {err['line']})" if err.get("line") else ""
    if kind == "blocked":
        res.outcome, res.stage, res.bucket = "blocked", "AUTHORIZE", "BLOCKED_BY_SAFETY"
        res.message = f"The script stopped at a blocked call before this check: {err.get('message')}"
    elif kind == "depth":
        res.outcome = "skipped"
        res.message = f"The script stopped at a mutating call before this check (quick run): {err.get('message')}"
    elif err:
        res.outcome, res.stage, res.bucket = "error", "OPERATE", "SCRIPT_ERROR"
        res.message = f"The script stopped before this check ran: {err.get('message')}{where}"
        res.traceback = err.get("traceback")
    else:
        res.outcome = "skipped"
        res.message = "The script finished without reaching this check."
    return res


def _write_result(session: Session, run: CheckRun, res: CheckResult, result: ScriptResult,
                  logs_on: Optional[str] = None, override: Any = None) -> None:
    """Record one check's result. The script's logs/output go on one CheckRun of the
    group; the others point at it with ``script_logs_run_id`` (``logs_on``)."""
    now = utcnow()
    run.verdict = _verdict(res)
    run.failure_stage = res.stage if run.verdict not in (RunStatus.PASS, RunStatus.SKIPPED) else None
    run.failure_bucket = res.bucket if run.verdict not in (RunStatus.PASS, RunStatus.SKIPPED) else None
    run.message = res.message or ("Passed." if run.verdict == RunStatus.PASS else "")
    if res.calls:
        run.risk_tier = _tier_of(res.calls)
    evidence = {**dict(res.evidence), "calls": res.calls, "line": res.line, "traceback": res.traceback}
    if logs_on is None:
        evidence.update(logs=result.logs[-50:], script_output=result.output[-4000:] if result.output else "")
    else:
        evidence["script_logs_run_id"] = logs_on
    run.evidence = evidence
    run.finished_at = now
    run.duration_ms = res.ms if res.ms is not None else (
        int((now - run.started_at).total_seconds() * 1000) if run.started_at else None)
    session.add(run)
    for i, (stage, status, message, bucket, evidence) in enumerate(_stages(res)):
        session.add(StageOutcome(run_id=run.run_id, stage=stage, sort_order=i, outcome=status,
                                 message=message or "", failure_bucket=bucket, evidence=evidence,
                                 raw_excerpt=res.traceback if status.failed else None,
                                 started_at=run.started_at, finished_at=now, duration_ms=res.ms))
    actor = run.requested_by or "system"
    if run.verdict == RunStatus.BLOCKED_BY_SAFETY:
        record_audit(session, actor=actor, action="safety.block", entity_type="check_run",
                     entity_id=run.run_id, summary=res.message, request_source=run.request_source.value,
                     commit=False, after={"check_id": run.check_id, "target_id": run.target_id,
                                          "run_as_role": run.run_as_role, "risk_tier": run.risk_tier})
    if any(c.get("overridden") for c in res.calls):
        record_audit(session, actor=actor, action="safety.override", entity_type="check_run",
                     entity_id=run.run_id, summary="A script call ran under a safety override",
                     request_source=run.request_source.value, commit=False,
                     after={"override_reason": getattr(override, "reason", None),
                            "confirmed_target": getattr(override, "target_name", None),
                            "calls": [c for c in res.calls if c.get("overridden")]})


def _dynamic_check(session: Session, test: Test, res: CheckResult) -> Check:
    """Get-or-create the Check for a check name the script produced at run time."""
    with _dynamic_lock:
        existing = _script_checks(session, test.id).get(res.name)
        if existing is not None:
            return existing
        decl = {"name": res.name, "kind": res.kind, "role": res.role, "advisory": res.advisory,
                "description": res.description, "expected_stage": res.expected_stage}
        fields = _check_fields(test, decl, res.calls)
        fields["hints"]["dynamic"] = True
        check = Check(**fields)
        session.add(check)
        session.commit()
        session.refresh(check)
        return check


def _load_runs(session: Session, run_ids: list[str]) -> list[CheckRun]:
    rows = {r.run_id: r for r in session.exec(
        select(CheckRun).where(CheckRun.run_id.in_(run_ids))  # type: ignore[attr-defined]
    ).all()}
    return [rows[rid] for rid in run_ids if rid in rows]


def execute_script_group(run_ids: list[str]) -> None:
    """Run one Test's script against one target and record every check's CheckRun."""
    from app.db.session import session_scope
    from app.services.run_service import claim_check_runs, lock_owned_runs, target_display_name
    from app.services.safety import SafetyOverride

    started = utcnow()
    group_ids = list(run_ids)
    with session_scope() as session:
        run_ids = claim_check_runs(session, group_ids, started)
        if not run_ids:
            return
        group = _load_runs(session, group_ids)
        runs = [r for r in group if r.run_id in set(run_ids)]
        first = runs[0]
        checks = {c.id: c for c in session.exec(
            select(Check).where(Check.id.in_({r.check_id for r in group}))  # type: ignore[attr-defined]
        ).all()}
        names = {r.run_id: script_check_name(checks[r.check_id]) if r.check_id in checks else ""
                 for r in group}
        first_check = checks.get(first.check_id)
        test_id = first.test_id or ((first_check.hints or {}).get("script_test_id") if first_check else None)
        test = session.get(Test, test_id) if test_id else None
        target = session.get(Target, first.target_id) if first.target_id else None
        request = session.get(RunRequest, first.request_id) if first.request_id else None
        if test is None or not test.script:
            for r in runs:
                r.verdict, r.failure_bucket, r.failure_stage = RunStatus.ERROR, "SCRIPT_ERROR", "OPERATE"
                r.message = "This check's test no longer has a script."
                r.finished_at = utcnow()
                session.add(r)
            session.commit()
            return
        script, default_role = test.script, test.run_as_role
        target_view = _target_view(target)
        override = SafetyOverride.from_payload(request.override if request else None)
        params = dict((request.params if request else None) or {})
        depth, source = first.depth, first.request_source
        header = SimpleNamespace(**{k: getattr(first, k) for k in (
            "test_id", "request_id", "target_id", "requested_by", "request_source", "depth",
            "git_sha", "event_ref", "app_version")},
            workspace_name=target_display_name(target) or first.workspace_name)

    ctx = _context(target_view, depth=depth, request_source=source, override=override,
                   default_role=default_role, params=params)
    try:
        result = run_script(script, ctx)
    except Exception as exc:  # noqa: BLE001 - a runtime failure must still close the runs
        logger.exception("script runtime failed for test %s", test_id)
        result = ScriptResult(error={"kind": "error", "message": f"script runtime failed: {exc}"})

    with session_scope() as session:
        owned = lock_owned_runs(session, run_ids)
        runs = [r for r in _load_runs(session, run_ids) if r.run_id in owned]
        if not runs:
            logger.info("script group %s was terminated while running; its late result is dropped", run_ids)
            return
        logs_on = runs[0].run_id
        for run in runs:
            name = names[run.run_id]
            res = result.check(name)
            if res is None or res.outcome == "not_reached":
                res = _unreached(name, result)
            run.workspace_name = run.workspace_name or header.workspace_name
            _write_result(session, run, res, result, logs_on=None if run.run_id == logs_on else logs_on,
                          override=override)
        session.commit()

        # Checks the script produced that weren't declared literally (e.g. in a loop).
        recorded = set(names.values())
        if header.test_id and header.request_id:
            test = session.get(Test, header.test_id)
            for res in result.checks:
                if res.name in recorded or test is None:
                    continue
                check = _dynamic_check(session, test, res)
                run = CheckRun(
                    request_id=header.request_id, test_id=header.test_id, check_id=check.id,
                    target_id=header.target_id, requested_by=header.requested_by,
                    request_source=header.request_source, depth=header.depth,
                    evaluator=Evaluator.DETERMINISTIC,
                    expectation=Expectation.DENY if res.kind == "counterfactual" else Expectation.PASS,
                    gating=not res.advisory, run_as_role=res.role or test.run_as_role,
                    risk_tier=check.risk_tier, workspace_name=header.workspace_name,
                    git_sha=header.git_sha, event_ref=header.event_ref, app_version=header.app_version,
                    started_at=started, verdict=RunStatus.RUNNING)
                session.add(run)
                session.flush()
                _write_result(session, run, res, result, logs_on=logs_on, override=override)
            session.commit()


# ---------------------------------------------------------------------------
# Dry run (the editor's "Run now"): nothing is persisted
# ---------------------------------------------------------------------------
def dry_run(session: Session, *, script: str, target: Optional[Target], depth: Depth,
            actor: str, request_source: RequestSource = RequestSource.UI_MANUAL,
            override: Any = None, run_as_role: Optional[str] = None,
            params: Optional[dict] = None) -> dict:
    from app.services import locking

    analysis = analyze(script)
    if any((e.get("message") or "").startswith("Syntax error") for e in analysis["errors"]):
        return {"analysis": analysis, "result": None}
    lease = (locking.held(target.id, f"dryrun-{uuid.uuid4().hex[:12]}")
             if target is not None and locking.needs_lock(target.safety_class) else nullcontext(True))
    target_view = _target_view(target)
    with lease as acquired:
        if not acquired:
            raise TargetBusy(f"target '{target.name}' is busy with another run; try again shortly")
        ctx = _context(target_view, depth=depth, request_source=request_source, override=override,
                       default_role=run_as_role, params=params)
        result = run_script(script, ctx)
    if target is not None and target.safety_class.is_mutating and depth == Depth.FULL:
        record_audit(session, actor=actor, action="test.dry_run", entity_type="target",
                     entity_id=target.id, request_source=request_source.value,
                     summary=f"Dry-ran a test script at full depth on {target.name}",
                     after={"calls": [c for ch in result.checks for c in ch.calls] + result.setup_calls})
    return {"analysis": analysis, "result": result.to_dict(),
            "target": {"id": target.id, "name": target.name,
                       "safety_class": target.safety_class.value} if target else None,
            "depth": depth.value}
