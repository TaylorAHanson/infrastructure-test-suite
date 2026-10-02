# Service Principal Role Assumption Mechanics in Databricks RBAC

**Bottom line for the assume-role decision (#25/#26):**

- **CI-Safe Non-Interactive Assumption:** M2M OAuth service principals CAN assume roles non-interactively using the `assume_group` parameter in token requests (no human browser interaction required). However, the RBAC limitations page explicitly states "An app's service principal cannot yet assume a role for programmatic (machine-to-machine) data access," creating an ambiguity about which service principal types can assume roles. The M2M OAuth documentation clearly supports role assumption, but this may be in conflict with or superseded by the limitations note.

- **Granularity (Named User vs Role):** Only **role/group assumption** is supported. You cannot assume a specific named user identity. Roles in Databricks are implemented as groups; assumption granularity is at the group level only.

---

## 1. Exact Token/API/SDK Flow for Acquiring Role's Effective Permissions

### M2M OAuth Service Principal Flow (Non-Interactive)

To acquire a role's permissions via M2M OAuth, a service principal makes a POST request to the Databricks token endpoint with the `assume_group` parameter:

```bash
curl --request POST \
  --url <token-endpoint-URL> \
  --user "$CLIENT_ID:$CLIENT_SECRET" \
  --data 'grant_type=client_credentials&scope=all-apis&assume_group=<group-id>'
```

- **Parameters:**
  - `grant_type`: Must be `client_credentials`
  - `scope`: Use `all-apis` for full scope
  - `assume_group`: The numeric group ID of the role (required to assume that role)
  - Client credentials: Service principal's `client_id` and `client_secret` in HTTP Basic Auth

- **Response:** An OAuth access token valid for one hour, scoped to the assumed role's permissions.

**Source:** https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m

### U2M OAuth User Flow (Interactive)

For interactive users, add `assume_group=<group-id>` to the OAuth authorization URL:

```
https://<databricks-instance>/oauth2/authorize?...&assume_group=<group-id>
```

The user completes browser-based OAuth flow, and the resulting token is scoped to the assumed role.

Alternatively, use CLI: `databricks auth login --host <workspace-url>` and select the role in the browser.

**Source:** https://docs.databricks.com/aws/en/dev-tools/auth/oauth-u2m and https://docs.databricks.com/aws/en/dev-tools/cli/authentication

### What Changes About the Credential/Session

When a role is assumed:
- The service principal's (or user's) own permissions are **replaced** (not combined) by the role's permissions
- All subsequent API calls using the token operate exclusively under the assumed role's authorization
- Actions are attributed to both the original identity (`run_by`) and the assumed role (`run_as`) in audit logs

**Source:** https://docs.databricks.com/aws/en/security/auth/rbac/ and https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m

---

## 2. Can M2M OAuth Service Principals Assume Roles Non-Interactively (CI-Safe)?

### Documented Capability

**Yes, according to M2M OAuth documentation:** "A service principal can assume a role when it requests a workspace-level access token. When a service principal assumes a role, the role's permissions replace the service principal's own."

The flow requires only:
- Service principal credentials (client_id, client_secret)
- The target group/role ID
- An HTTP POST request with the `assume_group` parameter

No human interaction or browser is required. This is designed for CI/CD pipelines.

**Source:** https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m

### Documented Limitation (Conflicting)

However, the RBAC limitations page states: "An app's service principal cannot yet assume a role for programmatic (machine-to-machine) data access."

### Ambiguity and Reconciliation

The documentation does not clearly define what "app's service principal" means. It may refer to:
- Service principals of Databricks deployed apps (as opposed to OAuth service principals)
- A subset of service principals with specific constraints

The M2M OAuth page's language ("A service principal can assume a role...") appears to contradict the limitations page. Given that the M2M OAuth page explicitly documents the `assume_group` parameter for service principals, **the current guidance is that standard OAuth service principals can assume roles non-interactively for CI/CD use cases**, but there may be unresolved limitations for "app" service principals specifically.

**Source:** https://docs.databricks.com/aws/en/security/auth/rbac/limitations

### CI-Safety Assessment

For conventional CI/CD automation:
- ✅ No interactive login or browser required
- ✅ Fully automated via REST API
- ✅ Based on machine credentials (client_id/secret)
- ⚠️ Assumes the limitations page caveat about "app service principals" does not apply to your use case

---

## 3. Session Scope and Expiration

### Token Lifetime

All M2M OAuth tokens, including role-assumed tokens, are **valid for exactly one hour**. After expiration, a new token must be requested.

**Source:** https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m

### Scope: Workspace-Level Only

The `assume_group` parameter is **supported only for workspace-level tokens**. 

- **Workspace-level tokens:** Can call REST APIs within a single workspace; `assume_group` works.
- **Account-level tokens:** Can call both account-level and workspace-level APIs across multiple workspaces; `assume_group` parameter causes the request to fail.

If you include `assume_group` in an account-level token request, the request fails.

**Source:** https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m

### Per-Call vs Per-Token

The assumed role is bound to the **token**, not to individual API calls. Once issued, a workspace-level token with an assumed role maintains that assumption for its entire one-hour lifetime. You cannot switch roles mid-token; you must request a new token to assume a different role.

**Source:** https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m

---

## 4. How Role Assumption Surfaces in Audit Logs

### Audit Log Location and Fields

Role assumption activity is recorded in the system audit table: `system.access.audit`

The identity_metadata field captures:
- **`run_by`:** The actual identity performing the action (the service principal or user)
- **`run_as`:** The assumed role under which the action is executed

**Source:** https://docs.databricks.com/aws/en/admin/system-tables/audit-logs

### Event Names and Specific Fields

The provided documentation does not specify dedicated event names for role assumption (e.g., `AssumeRole`, `SwitchRole`, etc.). Role assumption is recorded implicitly through the presence of both `run_by` and `run_as` in the identity_metadata; when `run_as` differs from `run_by`, role assumption is occurring.

### Recommended Audit Pattern

To audit role assumption:
1. Query `system.access.audit` for rows where `identity_metadata.run_as` is not null or differs from `identity_metadata.run_by`
2. Filter by `action_name` for specific actions performed as a role
3. Join on `request_params` to correlate with assume_group values if needed

**Source:** https://docs.databricks.com/aws/en/admin/system-tables/audit-logs

---

## 5. Constraints and Boundaries

### Account-Level vs Workspace-Level

| Aspect | Account-Level | Workspace-Level |
|--------|---------------|-----------------|
| **assume_group support** | ❌ Not supported; request fails | ✅ Supported |
| **API scope** | Both account-level and workspace-level APIs | Workspace-level APIs only |
| **Role assumption** | Cannot assume roles | Can assume roles |

**Source:** https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m

### Cloud Platform Availability

RBAC and role assumption are available on:
- ✅ AWS (fully documented)
- ✅ Azure (referenced in doc structure but not fully elaborated)
- ✅ GCP (referenced in doc structure but not fully elaborated)

The AWS documentation is authoritative; Azure and GCP parity is implied but not explicitly confirmed in the researched sources.

**Source:** https://docs.databricks.com/aws/en/security/auth/rbac/ and https://docs.databricks.com/en/security/auth/rbac/

### Permission Requirements

To assume a role:
- The service principal (or user) must have the **Assume** permission on the target group/role
- This permission is granted through group management interfaces
- Without Assume permission, the token request fails

**Source:** https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m

### Other Limitations

Several features do not work when acting as a role:
- Creating agents or managing alerts as a role is unsupported
- Lakeflow pipelines cannot be used with roles
- Vector Search index creation fails when assuming a role (querying works)
- Serverless Usage Policy creation fails (workspace dropdown becomes empty)
- Maximum 100 groups for workspace asset sharing controls
- Power BI ODBC connections may fail with 10+ concurrent connections when RBAC is enabled

**Source:** https://docs.databricks.com/aws/en/security/auth/rbac/limitations

---

## 6. Granularity: Named User vs Role/Group

### Role/Group Granularity Only

Databricks RBAC supports **only role/group assumption**, not individual named user identity assumption.

**Key principle:** "In Databricks, a role is implemented as a group."

Users (and service principals with Assume permission) can only assume roles that are backed by groups. A user can only act as one role at a time during a session.

**Source:** https://docs.databricks.com/aws/en/security/auth/rbac/

### No Named User Assumption

There is no documented mechanism to assume a specific named user's identity. The granularity boundary is at the group/role level; you cannot downgrade from a role assumption to a particular named user's identity.

**Source:** https://docs.databricks.com/aws/en/security/auth/rbac/ and https://docs.databricks.com/aws/en/security/auth/rbac/switch-roles

---

## 7. Feature Maturity and GA Status

### Current Status: Preview

RBAC is currently in **Preview** status, not GA. The documentation states:

> "An account admin must first enable the **Role-based access control (RBAC)** setting on the account Previews page."

Enabling RBAC requires administrative action through the account-level Previews page, indicating it is an opt-in preview feature.

**Last updated:** August 19, 2026

**Source:** https://docs.databricks.com/aws/en/security/auth/rbac/

### Implications for Feature Building

- ✓ RBAC is mature enough for design and POC work
- ⚠️ Not yet in GA; breaking changes to the API or permissions model may occur
- ⚠️ The limitation note ("An app's service principal cannot yet assume a role...") suggests ongoing development
- ⚠️ Dependent on RBAC being enabled at the account level; not available by default

---

## Summary of Key Technical Facts

| Question | Answer | Source |
|----------|--------|--------|
| **Token lifetime** | 1 hour | M2M OAuth docs |
| **Assume mechanism** | HTTP POST with `assume_group=<group-id>` parameter | M2M OAuth docs |
| **Scope limitation** | Workspace-level tokens only; account-level fails | M2M OAuth docs |
| **Permission required** | `Assume` permission on target group | M2M OAuth docs |
| **Granularity** | Roles (groups) only; no named user assumption | RBAC docs |
| **CI-Safe** | Yes (but with "app service principal" caveat) | M2M OAuth + Limitations docs |
| **Cloud availability** | AWS (full), Azure/GCP (implied but not elaborated) | RBAC docs |
| **Feature status** | Preview (not GA) | RBAC docs |

---

## Primary Sources

- https://docs.databricks.com/aws/en/security/auth/rbac/
- https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m
- https://docs.databricks.com/aws/en/dev-tools/auth/oauth-u2m
- https://docs.databricks.com/aws/en/dev-tools/cli/authentication
- https://docs.databricks.com/aws/en/security/auth/rbac/limitations
- https://docs.databricks.com/aws/en/admin/system-tables/audit-logs
