"""Closed enums that remain closed on purpose.

Under the rebuilt model (ADR-0001, amended by ADR-0017) most of the old
"vocabulary" enums are gone: the stage spine and the failure taxonomy are now
**data**, not closed Python enums (see :mod:`app.core.stages` and
:mod:`app.core.taxonomy`), so a new stage or failure bucket is a configuration
entry rather than a code migration.

What stays an enum here is the genuinely small, closed vocabulary:

* :class:`ProbeOutcome` — the per-stage outcome primitive (pass/fail/skip/error).
* :class:`RunStatus`   — a Run's lifecycle + terminal verdict.
* :class:`Evaluator`   — how a Check's assertion is judged (ADR-0008).
* :class:`Expectation` — whether a Check expects success or a denial (counterfactual).
* :class:`RiskTier`    — the coarse destructiveness tier (ADR-0004/0017).
* :class:`SafetyClass` — a Target's max-risk cap (ADR-0003/0004).
* :class:`Depth`       — the coarse quick/full run dial (ADR-0007).
* :class:`RequestSource` / :class:`CleanupPolicy` — small operational vocab.
"""
from __future__ import annotations

from enum import Enum


class ProbeOutcome(str, Enum):
    """Outcome of a single stage on the canonical spine."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ERROR = "ERROR"

    @property
    def failed(self) -> bool:
        return self in (ProbeOutcome.FAIL, ProbeOutcome.ERROR)


class RunStatus(str, Enum):
    """Lifecycle + terminal verdict of a Run (ADR-0005).

    ``running`` covers the non-terminal phases; the terminal verdict is one of
    ``PASS`` / ``FAIL`` / ``ERROR`` / ``BLOCKED_BY_SAFETY``. Gating is
    fail-closed: only ``PASS`` passes a pipeline.
    """

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    BLOCKED_BY_SAFETY = "BLOCKED_BY_SAFETY"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = frozenset(
    {
        RunStatus.PASS,
        RunStatus.FAIL,
        RunStatus.ERROR,
        RunStatus.BLOCKED_BY_SAFETY,
        RunStatus.SKIPPED,
        RunStatus.CANCELLED,
    }
)


class Evaluator(str, Enum):
    """How a Check's assertion is judged (ADR-0008).

    ``deterministic`` — a structured predicate over evidence; same evidence,
    same verdict; the CI default and the only kind that may gate.
    ``agentic`` — a pinned LLM judge; always advisory, never a fail-closed gate.
    """

    DETERMINISTIC = "deterministic"
    AGENTIC = "agentic"


class TestStatus(str, Enum):
    """A Test's Test Studio lifecycle state (ADR-0006 §8).

    ``draft`` — authored (by the Assistant or a human) but **not yet accepted**;
    a Draft **never runs** until a human explicitly promotes it. ``active`` — the
    accepted, runnable state the nightly "all" run and every trigger select.
    Editing an Active Test creates a new version (versioning, ADR-0001).
    """

    DRAFT = "draft"
    ACTIVE = "active"


class Expectation(str, Enum):
    """What a Check asserts about its operation's outcome.

    ``PASS`` — the operation should succeed (the ordinary assertion).
    ``DENY`` — the operation should be **denied / fail closed** at an expected
    stage (a counterfactual: pass = it was correctly blocked).
    """

    PASS = "PASS"
    DENY = "DENY"


class RiskTier(str, Enum):
    """Coarse destructiveness of an operation (ADR-0004, as amended by ADR-0017).

    Derived from the operation's verb/category, not hand-authored per op. R3 is
    the global never-floor enforced by the blocklist.
    """

    R0 = "R0"  # read
    R1 = "R1"  # safe mutation (create)
    R2 = "R2"  # destructive mutation (delete)
    R3 = "R3"  # catastrophic (globally forbidden)

    @property
    def order(self) -> int:
        return {"R0": 0, "R1": 1, "R2": 2, "R3": 3}[self.value]


class SafetyClass(str, Enum):
    """A Target's authoritative cap on the max risk tier it will run (ADR-0003)."""

    READ_ONLY = "read-only"
    SANDBOX = "sandbox"
    PROD_FOR_US = "prod-for-us"

    @property
    def is_mutating(self) -> bool:
        """Whether operations against this target can mutate state.

        Mutating targets (`sandbox`/`prod-for-us`) are exactly where a
        one-confirm operation-specific risk tier is captured (ADR-0017 §3).
        """
        return self in (SafetyClass.SANDBOX, SafetyClass.PROD_FOR_US)

    @property
    def max_risk_tier(self) -> "RiskTier":
        """The highest risk tier this class will run *without* an override.

        The explicit, inspectable class→tier matrix (ADR-0004 §1, user story 16):
        ``read-only`` → R0, ``prod-for-us`` → R1, ``sandbox`` → R2. **R3 is never
        reachable** by any class — it is the blocklist's global floor (ADR-0017).
        """
        return MAX_TIER_BY_CLASS[self]


# The class→max-risk-tier matrix (ADR-0004 §1). Kept as an explicit, inspectable
# table so the cap is auditable (user story 16). R2 on ``prod-for-us`` is only
# reachable via the human-only override; R3 is never in the table.
MAX_TIER_BY_CLASS: dict["SafetyClass", "RiskTier"] = {
    SafetyClass.READ_ONLY: RiskTier.R0,
    SafetyClass.PROD_FOR_US: RiskTier.R1,
    SafetyClass.SANDBOX: RiskTier.R2,
}

#: The single tier an override may elevate to, and only on ``prod-for-us``
#: (ADR-0004 §5): R2. R3 stays forbidden even under override.
OVERRIDE_ELEVATED_TIER: "RiskTier" = RiskTier.R2
OVERRIDE_ELIGIBLE_CLASS: "SafetyClass" = SafetyClass.PROD_FOR_US


class Depth(str, Enum):
    """The coarse how-far-up-the-spine dial on a Run (ADR-0007).

    ``QUICK`` runs the connectivity end of the spine (through AUTHORIZE);
    ``FULL`` runs through OPERATE/VERIFY. Defaults to QUICK; FULL is opt-in.
    """

    QUICK = "quick"
    FULL = "full"


class RequestSource(str, Enum):
    """Where a run request originated."""

    UI_MANUAL = "UI_MANUAL"
    SCHEDULE = "SCHEDULE"
    API = "API"
    CI = "CI"
    NIGHTLY = "NIGHTLY"


class CleanupPolicy(str, Enum):
    """How temporary artifacts a handler creates are cleaned up."""

    ALWAYS = "ALWAYS"
    ON_SUCCESS = "ON_SUCCESS"
    NEVER = "NEVER"
