"""The safety gate — identity-bounded blast-radius enforcement (ADR-0017/0004).

This is the platform's most important safeguard. It decides, per Check
operation, whether the operation may run against a given Target, in the
**identity-bounded order of strength** ADR-0017 established (per-op risk tier is
no longer primary):

* **(a) Least-privilege identity is the primary bound.** The per-target SP (or a
  registered assumed role, ADR-0018) physically caps what can run: a destructive
  call on a ``read-only`` Target simply fails with a permission error, safe by
  construction. The app can't reach into Databricks IAM, so what this gate
  enforces *for* layer (a) is the **role-registration guard** (a role may never
  out-run its Target's class, :mod:`app.services.roles`) — the load-bearing
  control that keeps the primary bound intact under assumption.
* **(b) Blocklist — the global R3-never floor.** A blocklisted (catastrophic)
  operation is refused on *every* class, override or not (:mod:`app.core.operations`).
* **(c) Coarse verb-derived risk tier vs the Target's safety class** — the
  *secondary* cap. The effective tier (coarse guess, raised by a one-confirm
  operation-specific tier on mutating targets) must not exceed the class matrix.

A blocked operation yields :data:`RunStatus.BLOCKED_BY_SAFETY` — never a pass,
never a functional failure — surfaced loudly. The only escape from (c) is the
**human-only, GUI-only, typed-confirmation** override for a single elevated tier
(R2) on a ``prod-for-us`` Target; it is **rejected for any machine identity**
(CI/automation) and can never reach R3 (b still applies).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.enums import (
    OVERRIDE_ELEVATED_TIER,
    OVERRIDE_ELIGIBLE_CLASS,
    RequestSource,
    RiskTier,
    RunStatus,
    SafetyClass,
)
from app.core.operations import Operation, is_blocklisted
from app.services.risk import effective_risk_tier

#: The request sources that represent a **human at the GUI**. The override is
#: GUI-only (ADR-0004 §5 / ADR-0010): only a manual UI action may carry one.
#: Everything else — API scripts, CI, nightly, schedules — is a machine identity
#: for override purposes and can never escalate its own blast radius.
_HUMAN_GUI_SOURCES = frozenset({RequestSource.UI_MANUAL})

#: The AWS-style phrase the friction ceremony makes a human type to confirm a
#: safety-gate-defeating edit (ADR-0010). Matched case-insensitively, trimmed.
CEREMONY_CONFIRMATION_PHRASE = "i understand"


def ceremony_rejection(typed_confirmation: str, reason: str) -> Optional[str]:
    """Return None if the friction ceremony passed, else why it did not.

    The ceremony is **friction, not permission** (ADR-0010): the backend only
    enforces that the human typed the confirmation phrase *and* gave a reason
    (which the audit record captures). The escalating-warning UX and the concrete
    consequence example are the GUI's; here we check the two load-bearing inputs.
    """
    if (typed_confirmation or "").strip().lower() != CEREMONY_CONFIRMATION_PHRASE:
        return (f"type '{CEREMONY_CONFIRMATION_PHRASE}' to confirm this "
                f"safety-gate-defeating change")
    if not (reason or "").strip():
        return "a reason is required and recorded in the audit log"
    return None


@dataclass(frozen=True)
class SafetyOverride:
    """A per-run authorization to elevate one operation (ADR-0004 §5).

    Consumed by the gate for exactly one elevated tier on a ``prod-for-us``
    Target. The ceremony *friction* (escalating warnings, the typed
    confirmation UX) is Spec 6; here the gate only checks that a valid,
    typed-confirmation override was captured and that the caller is human.
    """

    target_name: str  #: the value the human typed to confirm (must match target)
    reason: str = ""
    confirmed: bool = True

    @classmethod
    def from_payload(cls, payload: Optional[dict]) -> Optional["SafetyOverride"]:
        if not payload:
            return None
        return cls(
            target_name=str(payload.get("target_name", "")),
            reason=str(payload.get("reason", "")),
            confirmed=bool(payload.get("confirmed", True)),
        )


@dataclass
class SafetyDecision:
    """The gate's verdict for one operation against one Target."""

    allowed: bool
    effective_tier: RiskTier
    max_tier: RiskTier
    #: Which layer decided: ``blocklist`` | ``tier`` | ``override`` | ``identity``.
    layer: str = "tier"
    overridden: bool = False
    reason: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def status(self) -> RunStatus:
        return RunStatus.PASS if self.allowed else RunStatus.BLOCKED_BY_SAFETY


def is_machine_source(request_source: Any) -> bool:
    """Whether a request source is a machine identity for override purposes."""
    try:
        src = request_source if isinstance(request_source, RequestSource) else RequestSource(request_source)
    except ValueError:
        return True  # unknown source → treat as machine, fail safe
    return src not in _HUMAN_GUI_SOURCES


def evaluate_operation_safety(
    operation: Any,
    safety_class: Optional[SafetyClass],
    *,
    confirmed_tier: Optional[RiskTier] = None,
    override: Optional[SafetyOverride] = None,
    request_source: Any = RequestSource.API,
    target_name: Optional[str] = None,
) -> SafetyDecision:
    """Decide whether ``operation`` may run against a Target of ``safety_class``.

    Layers are applied in ADR-0017's order of strength; the returned decision
    records which layer fired (for audit), but callers should assert on the
    outcome (``allowed`` / :attr:`SafetyDecision.status`), not the layer.
    """
    op = operation if isinstance(operation, Operation) else Operation.from_dict(operation)

    # (b) Blocklist — the global R3-never floor. Refused on every class, and no
    # override can lift it: it is checked first and short-circuits.
    blocked, reason = is_blocklisted(op)
    if blocked:
        return SafetyDecision(
            allowed=False, effective_tier=RiskTier.R3, max_tier=RiskTier.R3,
            layer="blocklist", reason=f"Operation is blocklisted (R3-never floor): {reason}",
        )

    # An unregistered target (None class) has no authoritative cap; refuse — a
    # never-registered workspace is untouchable (user story 1). This should not
    # happen post-dispatch, but the gate fails closed if it does.
    if safety_class is None:
        return SafetyDecision(
            allowed=False, effective_tier=RiskTier.R0, max_tier=RiskTier.R0,
            layer="identity", reason="No target safety class: target is not registered.",
        )

    # (c) Coarse verb-derived tier (raised by any one-confirm tier) vs the class.
    effective = effective_risk_tier(op, confirmed_tier)
    max_tier = safety_class.max_risk_tier

    if effective.order <= max_tier.order:
        return SafetyDecision(
            allowed=True, effective_tier=effective, max_tier=max_tier, layer="tier",
            reason=f"{effective.value} within {safety_class.value} cap ({max_tier.value}).",
        )

    # Over the cap — the only escape is a valid human override for exactly one
    # elevated tier (R2) on a prod-for-us target.
    decision = SafetyDecision(
        allowed=False, effective_tier=effective, max_tier=max_tier, layer="tier",
        reason=(f"{effective.value} exceeds the {safety_class.value} cap "
                f"({max_tier.value}): BLOCKED_BY_SAFETY."),
    )
    override_error = _override_rejection(
        override, effective, safety_class, request_source, target_name)
    if override is not None and override_error is None:
        return SafetyDecision(
            allowed=True, effective_tier=effective, max_tier=max_tier,
            layer="override", overridden=True,
            reason=(f"Human override authorized {effective.value} on "
                    f"{safety_class.value} (reason: {override.reason or 'n/a'})."),
            evidence={"override_reason": override.reason, "confirmed_target": override.target_name},
        )
    if override is not None:
        decision.reason = f"Override rejected: {override_error}"
    return decision


def _override_rejection(
    override: Optional[SafetyOverride],
    effective: RiskTier,
    safety_class: SafetyClass,
    request_source: Any,
    target_name: Optional[str],
) -> Optional[str]:
    """Return None if the override authorizes this op, else the reason it doesn't."""
    if override is None:
        return "no override supplied"
    if is_machine_source(request_source):
        # The hard rule: automation can never escalate its own blast radius.
        return "overrides are human-only and GUI-only, never available to CI/automation"
    if not override.confirmed:
        return "typed confirmation was not completed"
    if safety_class != OVERRIDE_ELIGIBLE_CLASS:
        return f"an override applies only to '{OVERRIDE_ELIGIBLE_CLASS.value}' targets"
    if effective.order != OVERRIDE_ELEVATED_TIER.order:
        # One elevated tier only: R2. R3 is caught by the blocklist above; a tier
        # beyond R2 is never override-able.
        return f"an override authorizes exactly one elevated tier ({OVERRIDE_ELEVATED_TIER.value})"
    if target_name is not None and override.target_name != target_name:
        return "typed confirmation did not match the target name"
    return None
