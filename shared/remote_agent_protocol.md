# Remote-agent protocol (control plane <-> remote executor)

A run from inside the Databricks App **cannot** prove outside-in connectivity
from a user desktop, corporate network, BI tool, or on-prem system. For those
cases the app is the **control plane and result store**, and a **remote agent**
runs at the relevant network vantage point and reports **signed** results back.

The first iteration ships the protocol + a control-plane stub
(`executor_key = remote_agent`). Real agents are separate executors deployed at
the vantage point (e.g. a small process on a Power BI gateway host, a corp
jumpbox, or next to an on-prem Oracle instance).

## 1. Register

```
POST /api/v1/agents/register
{
  "name": "corp-powerbi-gw-01",
  "vantage_point": "bi-network",
  "capabilities": ["powerbi_connectivity", "tableau_connectivity"],
  "signing_key_id": "agent-key-1"
}
```

## 2. Heartbeat

```
POST /api/v1/agents/{agent_id}/heartbeat
```

## 3. Claim work

The control plane creates runs whose `executor_kind = REMOTE_AGENT`. Those runs
are produced in a PARTIAL/awaiting state with `root_cause = EXTERNAL_AGENT_UNAVAILABLE`
until an agent posts results. (A pull-based `GET /agents/{id}/work` queue is the
intended extension; the current iteration accepts pushed results for a known
`run_id`.)

## 4. Post signed results

```
POST /api/v1/agents/results
{
  "run_id": "run_...",
  "agent_id": "agent_...",
  "signature": "<hex hmac-sha256>",
  "steps": [
    {"step_kind": "AUTH",       "outcome": "PASS", "connectivity": "CONNECTABLE", "message": "..."},
    {"step_kind": "NETWORK",    "outcome": "PASS", "connectivity": "CONNECTABLE", "message": "..."},
    {"step_kind": "FUNCTIONAL", "outcome": "PASS", "connectivity": "CONNECTABLE", "message": "...",
     "evidence": {"query_ms": 42}}
  ]
}
```

### Signature

HMAC-SHA256 over the canonical JSON of `{run_id, agent_id, steps}` with
`sort_keys=true` and compact separators, keyed by the shared secret
(`REMOTE_AGENT_SIGNING_SECRET`, sourced from a Databricks secret scope):

```python
import hashlib, hmac, json
canonical = json.dumps({"run_id": run_id, "agent_id": agent_id, "steps": steps},
                       sort_keys=True, separators=(",", ":"))
signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
```

The control plane verifies the signature (when a secret is configured), folds
the steps into the run, and re-aggregates connectable/functional outcomes.
In dev (no secret configured) unsigned results are accepted for convenience.

## Step contract

Every agent should answer the same six questions the in-app executors answer:
`PRECHECK -> AUTH -> DNS -> NETWORK -> FUNCTIONAL -> CLEANUP`, classifying any
failure into a `root_cause_bucket` from [`enums.json`](enums.json).
