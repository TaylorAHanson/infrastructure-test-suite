"""Security posture: drift checks, kept out of the health gate.

These say whether a workspace is configured the way security wants, not whether
it works, so they live in their own Suite and never join the CI Gate. Most read
admin-only APIs (workspace settings, every user's tokens, IP access lists), so
run them with an identity that is a workspace admin; a non-admin gets
PERMISSION_DENIED, which is reported as a failure rather than hidden. For a
broader battery, Databricks' Security Analysis Tool (SAT) covers far more.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

EXFILTRATION = '''"""Exfiltration controls

Notebook export, results download and notebook/table clipboard copy are all
switched off. An unset setting means the platform default, which is on.
Needs a workspace-admin identity to read workspace settings.
"""
KEYS = ["enableExportNotebook", "enableResultsDownloading", "enableNotebookTableClipboard"]

with check("notebook export, results download and clipboard copy are off"):
    conf = w.workspace_conf.get_status(keys=",".join(KEYS))
    record("settings", conf)
    still_on = [k for k in KEYS if str(conf.get(k)).lower() != "false"]
    assert not still_on, "still enabled (unset means on): " + ", ".join(still_on)
'''

TOKEN_HYGIENE = '''"""Token hygiene

A maximum lifetime is set for new personal access tokens, and no existing token
is non-expiring. Needs a workspace-admin identity (token management is admin-only).

Target tags (or run params):
  max_token_lifetime_days   the most a policy may allow (default 90)
"""
LIMIT_DAYS = int(setting("max_token_lifetime_days", 90))

with check("new tokens have a bounded lifetime"):
    conf = w.workspace_conf.get_status(keys="maxTokenLifetimeDays")
    days = conf.get("maxTokenLifetimeDays")
    record("max_token_lifetime_days", days)
    assert days and int(days) > 0, "no maximum token lifetime is set, so new tokens can live forever"
    assert int(days) <= LIMIT_DAYS, f"tokens may live {days} days; the limit is {LIMIT_DAYS}"

with check("no personal access token is non-expiring"):
    tokens = w.token_management.list()
    forever = [t for t in tokens if (t.expiry_time or -1) <= 0]
    record("tokens", len(tokens))
    record("non_expiring", [{"owner": t.created_by_username, "comment": t.comment} for t in forever])
    assert not forever, f"{len(forever)} of {len(tokens)} tokens never expire"
'''

NETWORK_ACCESS = '''"""Network access controls

IP access lists are switched on and at least one ALLOW list is enabled, so the
workspace only accepts traffic from known networks. The quick-tier identity
check already catches the dangerous direction (a list that locks out automation).
Needs a workspace-admin identity.
"""
with check("IP access lists are enforced"):
    conf = w.workspace_conf.get_status(keys="enableIpAccessLists")
    record("enabled", conf.get("enableIpAccessLists"))
    assert str(conf.get("enableIpAccessLists")).lower() == "true", "IP access lists are switched off"
    lists = w.ip_access_lists.list()
    record("lists", [{"label": l.label, "type": l.list_type, "enabled": l.enabled,
                      "addresses": l.address_count} for l in lists])
    allow = [l for l in lists if l.list_type == "ALLOW" and l.enabled]
    assert allow, "IP access lists are on, but no ALLOW list is enabled"
'''

USER_ISOLATION = '''"""User isolation

No cluster runs in the No Isolation Shared access mode, where users on the same
cluster can read each other's data and credentials. Clusters without an access
mode are legacy clusters with no isolation. Pipeline clusters are managed by
Databricks and left out. A non-admin identity only sees clusters it can access,
so run this as a workspace admin.
"""
UNISOLATED = (None, "NONE", "LEGACY_PASSTHROUGH")

with check("no cluster runs without user isolation"):
    clusters = [c for c in w.clusters.list()
                if c.cluster_source not in ("PIPELINE", "PIPELINE_MAINTENANCE")]
    unisolated = [c for c in clusters if c.data_security_mode in UNISOLATED]
    record("clusters", len(clusters))
    record("unisolated", [{"name": c.cluster_name, "id": c.cluster_id, "mode": c.data_security_mode}
                          for c in unisolated])
    assert not unisolated, f"{len(unisolated)} cluster(s) have no user isolation: " + ", ".join(
        c.cluster_name or c.cluster_id for c in unisolated[:10])
'''

ADMIN_FOOTPRINT = '''"""Admin footprint

The workspace admins group stays small, and few service principals hold admin.
The service-principal count is advisory: a validation identity that runs the
posture suite is itself an admin, and some automation legitimately needs it.

Target tags (or run params):
  max_workspace_admins           default 10
  max_admin_service_principals   default 2
"""
admins = w.groups.list(filter='displayName eq "admins"', attributes="id,displayName,members")
members = (admins[0].members or []) if admins else []

with check("the workspace admins group stays small"):
    limit = int(setting("max_workspace_admins", 10))
    assert admins, "the workspace has no admins group"
    record("admins", sorted(m.display or m.value for m in members))
    assert len(members) <= limit, f"{len(members)} workspace admins; the limit is {limit}"

with check("few service principals are workspace admins", advisory=True):
    limit = int(setting("max_admin_service_principals", 2))
    sp_ids = {sp.id for sp in w.service_principals.list(attributes="id,displayName")}
    admin_sps = [m.display or m.value for m in members
                 if m.value in sp_ids or str(m.get("$ref") or "").startswith("ServicePrincipals/")]
    record("admin_service_principals", sorted(admin_sps))
    assert len(admin_sps) <= limit, f"{len(admin_sps)} service principals are admins; the limit is {limit}"
'''


TESTS: dict[str, TestSpec] = {
    "exfiltration": TestSpec("Exfiltration controls",
                             "Notebook export, results download, and clipboard copy are off.",
                             tags=["security", "posture"], script=EXFILTRATION),
    "token_hygiene": TestSpec("Token hygiene",
                              "New tokens have a bounded lifetime and no existing token is non-expiring.",
                              tags=["security", "identity", "posture"], script=TOKEN_HYGIENE),
    "network_access": TestSpec("Network access controls",
                               "IP access lists are on and an ALLOW list is enabled.",
                               tags=["security", "network", "posture"], script=NETWORK_ACCESS),
    "isolation": TestSpec("User isolation",
                          "No cluster runs in the permissive No Isolation Shared mode.",
                          tags=["security", "compute", "posture"], script=USER_ISOLATION),
    "admin_footprint": TestSpec("Admin footprint",
                                "The admins group stays small; few service principals are admins.",
                                tags=["security", "identity", "posture"], script=ADMIN_FOOTPRINT),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec("Security posture (drift)",
              "Configuration drift security cares about: exfiltration settings, token hygiene, IP "
              "access lists, user isolation, admin footprint. Needs a workspace-admin identity; "
              "never part of the health gate.",
              ["exfiltration", "token_hygiene", "network_access", "isolation", "admin_footprint"],
              tags=["seeded", "security", "posture"]),
]


CONTRIBUTION = Contribution(
    tests=TESTS,
    suites=SUITES,
)
