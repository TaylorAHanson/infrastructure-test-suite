"""Host-identity access model (ADR-0010) — *identify*, never *authorize*.

The platform ships **no app-local authorization**: authentication is the host
Databricks App identity and **any authenticated human has full capability**.
So this module has exactly one job — resolve *who* the caller is (for the audit
trail, the standing forensic control) and whether they are a **human** or a
**machine** identity. It contains no role, ACL, or permission logic, because
there is none to contain: the preventive control lives in the host workspace's
grant of who may reach the App at all.

Identity resolution precedence:

1. **Forwarded human identity** — a Databricks App puts the signed-in user's
   identity in ``X-Forwarded-Email`` / ``X-Forwarded-Preferred-Username`` /
   ``X-Forwarded-User``. In a real deployment the platform sets these; a browser
   cannot spoof them, so this is the authoritative source.
2. **Forwarded machine identity** — a service-principal caller carries
   ``X-Forwarded-Client-Id`` with no human header; treated as ``machine``.
3. **Explicit ``actor`` query param** — a dev / programmatic affordance (and the
   seam the audit tests drive). Ignored in production, where (1) wins.
4. **Dev fallback** — an unauthenticated local caller is ``api-user`` (the prior
   default), so ``./dev.sh`` and existing scripts keep working.

Note the human/machine flag here is *for the audit record*. The **override**'s
machine rejection is keyed on the run's :class:`RequestSource` (the override is
GUI-only, ADR-0004/0010) — a separate, stronger signal — and is unaffected by
this dependency.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

#: Databricks Apps forward the signed-in human's identity in one of these.
_HUMAN_IDENTITY_HEADERS = (
    "x-forwarded-email",
    "x-forwarded-preferred-username",
    "x-forwarded-user",
)
#: A service-principal (machine) caller carries a client id but no human header.
_MACHINE_IDENTITY_HEADERS = ("x-forwarded-client-id",)

#: The dev/local fallback identity when nothing else identifies the caller.
DEFAULT_ACTOR = "api-user"


@dataclass(frozen=True)
class Actor:
    """The resolved host-identity of a request.

    ``kind`` is ``"user"`` (a human at the App) or ``"machine"`` (a service
    principal / automation). It feeds the audit record's ``actor_kind``; it is
    **not** an authorization — every human has full capability.
    """

    handle: str
    kind: str = "user"
    #: True when the identity came from a header the Databricks App proxy sets,
    #: False for the ``actor`` query param or the dev fallback.
    verified: bool = False

    @property
    def is_human(self) -> bool:
        return self.kind == "user"


def resolve_actor(request: Request, default: str = DEFAULT_ACTOR) -> Actor:
    """Resolve the caller's host identity (see module docstring for precedence)."""
    for header in _HUMAN_IDENTITY_HEADERS:
        value = request.headers.get(header)
        if value:
            return Actor(handle=value.strip(), kind="user", verified=True)

    for header in _MACHINE_IDENTITY_HEADERS:
        value = request.headers.get(header)
        if value:
            return Actor(handle=value.strip(), kind="machine", verified=True)

    explicit = request.query_params.get("actor")
    if explicit:
        return Actor(handle=explicit, kind="user")

    return Actor(handle=default, kind="user")


def current_actor(request: Request) -> Actor:
    """FastAPI dependency: the host-identity :class:`Actor` for this request."""
    return resolve_actor(request)


def actor_handle(default: str = DEFAULT_ACTOR):
    """FastAPI dependency factory: the audit handle for this request.

    The forwarded identity always wins, so a deployed app records who really
    made the change; ``?actor=`` and ``default`` only apply when no proxy header
    is present (local dev, tests).
    """
    def dependency(request: Request) -> str:
        return resolve_actor(request, default=default).handle

    return dependency
