"""The Databricks SDK/OpenAPI surface the Assistant grounds on (ADR-0017).

Under **open-SDK authoring** a Check may express *any* Databricks SDK/API call;
the ground truth for what may be expressed is "the live SDK/OpenAPI surface
**minus the blocklist**", not a curated manifest allowlist. The Assistant
grounds its write-path by searching this surface (``search_sdk_surface``) rather
than a hand-authored handler catalog — so it can author an operation that was
never pre-declared, while a blocklisted (R3) call is still refused.

In a full deployment this surface is introspected from the installed
``databricks-sdk`` / OpenAPI spec. Here it is a **representative, in-repo slice**
of that surface — deterministic for tests and useful on day one — carrying the
same shape the introspected surface would: a ``service.method`` + coarse verb,
a one-line summary, the canonical stages it maps onto, and optional
counterfactual / evidence *hints* (all that survives of the demoted "handler
manifest", ADR-0017 §5). It is **not an allowlist**: it is a searchable
reference, and anything on it minus the blocklist is expressible.

Every entry is filtered through the blocklist on the way out, so a catastrophic
operation can never be *suggested* even if it were listed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.operations import Operation, infer_verb, is_blocklisted


@dataclass(frozen=True)
class SdkOperation:
    """One entry on the grounding surface: a Databricks SDK/API operation."""

    service: str
    method: str
    summary: str
    #: Coarse HTTP-ish verb; inferred from the method name when omitted.
    verb: str = ""
    #: The canonical spine stages this operation exercises (a grounding hint).
    stages: tuple[str, ...] = ()
    #: Optional counterfactual hint: the paired denial phrasing an author would
    #: assert ("a non-compliant cluster is denied"), and the stage it's denied
    #: at. Its presence is what lets the Assistant *suggest* a counterfactual.
    counterfactual: Optional[dict] = None
    #: Evidence fields this operation typically surfaces (grounds an assertion).
    evidence_fields: tuple[str, ...] = ()
    #: Free-text keywords to widen intent matching beyond service/method.
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def resolved_verb(self) -> str:
        return (self.verb or infer_verb(self.method)).upper()

    @property
    def qualified(self) -> str:
        return f"{self.service}.{self.method}"

    def to_operation(self, params: Optional[dict] = None) -> Operation:
        return Operation(service=self.service, method=self.method,
                         verb=self.resolved_verb(), params=dict(params or {}))

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "method": self.method,
            "verb": self.resolved_verb(),
            "summary": self.summary,
            "stages": list(self.stages),
            "counterfactual": self.counterfactual,
            "evidence_fields": list(self.evidence_fields),
        }


_CONNECT_STAGES = ("RESOLVE", "CONNECT", "TLS", "AUTHENTICATE", "AUTHORIZE")
_FULL_STAGES = _CONNECT_STAGES + ("OPERATE", "VERIFY")

# A representative slice of the Databricks SDK surface. Deliberately spans read
# (R0), safe-mutation (R1), and destructive (R2) shapes across the services a
# ~100-target connectivity/regression sweep touches. Catastrophic (R3) calls are
# intentionally absent *and* filtered by the blocklist below.
SURFACE: list[SdkOperation] = [
    SdkOperation("clusters", "list", "List clusters in the workspace.",
                 stages=_FULL_STAGES, evidence_fields=("count",),
                 keywords=("compute", "cluster")),
    SdkOperation("clusters", "get", "Get one cluster's state.",
                 stages=_FULL_STAGES, evidence_fields=("state",),
                 keywords=("compute", "cluster")),
    SdkOperation("clusters", "create", "Create a cluster (honours the compute policy).",
                 stages=_FULL_STAGES,
                 counterfactual={
                     "assertion": "a non-compliant cluster is denied by policy",
                     "expected_stage": "AUTHORIZE",
                     "params": {"demo_outcome": "deny"},
                 },
                 evidence_fields=("cluster_id",),
                 keywords=("compute", "cluster", "policy", "compute-policy")),
    SdkOperation("clusters", "delete", "Terminate a cluster.",
                 stages=_FULL_STAGES, keywords=("compute", "cluster", "terminate")),
    SdkOperation("statement_execution", "execute_statement",
                 "Run a SQL statement on a warehouse.", verb="POST",
                 stages=_FULL_STAGES, evidence_fields=("row_count", "status_code"),
                 keywords=("sql", "warehouse", "query", "select")),
    SdkOperation("warehouses", "get", "Get a SQL warehouse's state.",
                 stages=_FULL_STAGES, evidence_fields=("state",),
                 keywords=("sql", "warehouse")),
    SdkOperation("catalogs", "list", "List Unity Catalog catalogs.",
                 stages=_FULL_STAGES, evidence_fields=("count",),
                 keywords=("unity", "catalog", "uc", "governance")),
    SdkOperation("schemas", "list", "List schemas in a catalog.",
                 stages=_FULL_STAGES, evidence_fields=("count",),
                 keywords=("unity", "catalog", "schema", "uc")),
    SdkOperation("grants", "get", "Read effective grants on a securable.",
                 stages=_FULL_STAGES, evidence_fields=("privileges",),
                 counterfactual={
                     "assertion": "the analyst profile is denied SELECT",
                     "expected_stage": "AUTHORIZE",
                 },
                 keywords=("unity", "grant", "permission", "privilege", "governance")),
    SdkOperation("jobs", "list", "List jobs in the workspace.",
                 stages=_FULL_STAGES, evidence_fields=("count",),
                 keywords=("job", "workflow", "lakeflow")),
    SdkOperation("jobs", "run_now", "Trigger a job run.", verb="POST",
                 stages=_FULL_STAGES, evidence_fields=("run_id",),
                 keywords=("job", "workflow", "trigger")),
    SdkOperation("dbfs", "list", "List a DBFS path.",
                 stages=_FULL_STAGES, evidence_fields=("count",),
                 keywords=("storage", "dbfs", "file")),
    SdkOperation("dashboards", "create", "Create a Lakeview dashboard.", verb="POST",
                 stages=_FULL_STAGES, evidence_fields=("dashboard_id",),
                 keywords=("dashboard", "lakeview", "bi")),
    SdkOperation("dashboards", "delete", "Delete a dashboard (destructive).",
                 stages=_FULL_STAGES, keywords=("dashboard", "lakeview")),
    SdkOperation("serving_endpoints", "query", "Query a Model Serving endpoint.", verb="POST",
                 stages=_FULL_STAGES, evidence_fields=("status_code",),
                 keywords=("model", "serving", "inference", "endpoint")),
    SdkOperation("current_user", "me", "Return the calling identity (whoami).",
                 stages=_CONNECT_STAGES, evidence_fields=("user_name",),
                 keywords=("identity", "auth", "whoami", "connectivity", "reachable")),
    SdkOperation("http", "get", "Perform an outbound HTTPS GET (egress probe).",
                 stages=_FULL_STAGES, evidence_fields=("status_code",),
                 keywords=("egress", "outbound", "network", "http", "https", "reach")),
]


def _visible(op: SdkOperation) -> bool:
    """An operation is groundable only if it is not on the R3 blocklist."""
    blocked, _ = is_blocklisted(op.to_operation())
    return not blocked


def all_operations() -> list[SdkOperation]:
    """The full groundable surface (blocklisted operations filtered out)."""
    return [op for op in SURFACE if _visible(op)]


def find_operation(service: str, method: str) -> Optional[SdkOperation]:
    """Look up one surface entry by ``service.method`` (blocklist-filtered)."""
    for op in all_operations():
        if op.service == service and op.method == method:
            return op
    return None


def _score(op: SdkOperation, terms: list[str]) -> int:
    """Crude relevance: how many query terms hit the entry's searchable text."""
    haystack = " ".join(
        [op.service, op.method, op.summary, " ".join(op.keywords)]
    ).lower()
    return sum(1 for t in terms if t and t in haystack)


def search_sdk_surface(query: str, limit: int = 8) -> list[SdkOperation]:
    """Rank the groundable surface against a free-text intent.

    The Assistant's ``search_sdk_surface`` tool (ADR-0017): given a plain-English
    intent, return the closest real SDK operations to ground authoring on. A
    blocklisted operation is never returned (it is filtered from the surface),
    so a suggestion can never name a catastrophic call.
    """
    terms = [t for t in (query or "").lower().replace(",", " ").split() if len(t) > 2]
    scored = [(op, _score(op, terms)) for op in all_operations()]
    hits = [op for op, s in sorted(scored, key=lambda x: x[1], reverse=True) if s > 0]
    return hits[:limit]
