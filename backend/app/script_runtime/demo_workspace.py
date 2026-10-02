"""A stand-in workspace for script runs with no Databricks connection (demo mode).

It answers like a healthy, correctly configured workspace that has the canary
fixtures (``docs/canary-fixtures.md``) provisioned, so the seeded Tests pass in
a demo and a hand-written script sees realistic shapes. It is stateful for the
length of one run: a row a script writes reads back, and a probe job's output
reflects the config the script submitted.

The canary contents here (one ``ping`` row whose ``note`` is ``canary``, a
``canary.txt`` file holding ``canary``, a ``forbidden`` table the identity
can't read) mirror what ``scripts/provision_canary.py`` creates.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

_GOOD_CONF = {
    "enableExportNotebook": "false",
    "enableResultsDownloading": "false",
    "enableNotebookTableClipboard": "false",
    "maxTokenLifetimeDays": "90",
    "enableIpAccessLists": "true",
    "enableTokensConfig": "true",
}

_DENIED = {"state": "FAILED", "error": {
    "error_code": "BAD_REQUEST",
    "message": "[INSUFFICIENT_PERMISSIONS] Insufficient privileges: User does not have SELECT on Table."}}


def _ok(rows: list[list[Any]], columns: list[str]) -> dict:
    return {"statement_id": "demo-statement", "status": {"state": "SUCCEEDED"},
            "manifest": {"schema": {"columns": [{"name": c} for c in columns]}},
            "result": {"data_array": [[None if v is None else str(v) for v in r] for r in rows]}}


class DemoWorkspace:
    def __init__(self, tags: Optional[dict] = None, params: Optional[dict] = None):
        self.settings = {**dict(tags or {}), **{k: v for k, v in dict(params or {}).items() if v not in (None, "")}}
        self.tables: dict[str, list[str]] = {}
        self.runs: dict[int, dict] = {}
        self._next_run = 1000

    # -- entry point ----------------------------------------------------
    def respond(self, kind: str, msg: dict) -> tuple[Any, Optional[dict]]:
        """``(value, None)`` for a success, ``(None, error)`` for a failure."""
        if kind == "http":
            return {"status_code": 200, "headers": {}, "text": "", "demo": True}, None
        if kind == "api":
            return {"demo": True}, None
        service, method = str(msg.get("service", "")), str(msg.get("method", ""))
        kwargs = msg.get("kwargs") or {}
        handler = getattr(self, f"_{service}__{method}", None)
        if handler is not None:
            return handler(**kwargs)
        if method.startswith("list"):
            return [{"id": "demo-1", "name": "demo-1", "state": "RUNNING", "demo": True}], None
        return {"demo": True}, None

    # -- identity and Unity Catalog ------------------------------------
    def _current_user__me(self, **_):
        return {"id": "demo", "user_name": "demo-service-principal", "display_name": "Demo service principal",
                "active": True}, None

    def _metastores__current(self, **_):
        return {"metastore_id": self.settings.get("expected_metastore_id") or "demo-metastore-001",
                "workspace_id": 1234567890, "default_catalog_name": "main"}, None

    def _catalogs__get(self, name: str = "", **_):
        return {"name": name, "owner": "terraform", "isolation_mode": "ISOLATED",
                "catalog_type": "MANAGED_CATALOG"}, None

    # -- SQL --------------------------------------------------------------
    def _warehouses__list(self, **_):
        return [{"id": "demo-warehouse", "name": "Serverless Starter Warehouse", "state": "RUNNING",
                 "enable_serverless_compute": True, "warehouse_type": "PRO"}], None

    def _warehouses__get(self, id: str = "", **_):  # noqa: A002 - the SDK's argument name
        return {"id": id, "name": "Classic warehouse", "state": "RUNNING",
                "enable_serverless_compute": False, "warehouse_type": "PRO"}, None

    def _warehouses__start(self, id: str = "", **_):  # noqa: A002
        return {"id": id}, None

    def _statement_execution__execute_statement(self, statement: str = "", **_):
        text = " ".join((statement or "").split())
        lower = text.lower()
        if "forbidden" in lower:
            return {"statement_id": "demo-statement", "status": _DENIED}, None
        if lower.startswith("list "):
            path = text.split("'")[1] if "'" in text else "/Volumes/demo"
            return _ok([[f"{path.rstrip('/')}/canary.txt", "canary.txt", 7, int(time.time() * 1000)]],
                       ["path", "name", "size", "modification_time"]), None
        if "read_files(" in lower:
            return _ok([["canary"]], ["value"]), None
        if lower.startswith("show catalogs"):
            return _ok([["main"], ["system"], [self.settings.get("canary_catalog") or "validation_canary"]],
                       ["catalog"]), None
        m = re.match(r"create or replace table\s+([\w.`]+)", lower)
        if m:
            self.tables[m.group(1)] = []
            return _ok([], []), None
        m = re.match(r"insert into\s+([\w.`]+)\s+values\s*\(\s*'([^']*)'", text, re.I)
        if m:
            self.tables.setdefault(m.group(1).lower(), []).append(m.group(2))
            return _ok([], ["num_affected_rows", "num_inserted_rows"]), None
        m = re.match(r"select\s+nonce\s+from\s+([\w.`]+)", lower)
        if m:
            return _ok([[n] for n in self.tables.get(m.group(1), [])], ["nonce"]), None
        if re.search(r"from\s+[\w.`]*\bping\b", lower):
            return _ok([[1, "canary"]], ["id", "note"]), None
        columns = re.findall(r"\bas\s+(\w+)", text, re.I) or ["value"]
        return _ok([["1"] * len(columns)], columns), None

    # -- compute ----------------------------------------------------------
    def _clusters__list_node_types(self, **_):
        return {"node_types": [{"node_type_id": "m5d.xlarge", "num_cores": 4, "memory_mb": 16384,
                                "category": "General Purpose", "is_deprecated": False, "is_hidden": False}]}, None

    def _clusters__spark_versions(self, **_):
        return {"versions": [{"key": "15.4.x-scala2.12",
                              "name": "15.4 LTS (includes Apache Spark 3.5.0, Scala 2.12)"}]}, None

    def _clusters__list(self, **_):
        return [{"cluster_id": "demo-shared", "cluster_name": "shared-analytics",
                 "data_security_mode": "USER_ISOLATION", "state": "TERMINATED", "cluster_source": "UI"}], None

    def _workspace__mkdirs(self, **_):
        return {}, None

    def _workspace__import_(self, **_):
        return {}, None

    def _jobs__submit(self, tasks: Optional[list] = None, **_):
        self._next_run += 2
        run_id = self._next_run
        task = (tasks or [{}])[0]
        params = ((task.get("notebook_task") or {}).get("base_parameters") or {})
        try:
            config = json.loads(params.get("config") or "{}")
        except ValueError:
            config = {}
        self.runs[run_id] = {"task_key": task.get("task_key") or "probe", "config": config,
                             "classic": bool(task.get("new_cluster"))}
        return {"run_id": run_id}, None

    def _jobs__get_run(self, run_id: int = 0, **_):
        run = self.runs.get(int(run_id or 0), {"task_key": "probe"})
        return {"run_id": run_id, "run_name": "validation probe",
                "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS", "state_message": ""},
                "status": {"state": "TERMINATED", "termination_details": {"code": "SUCCESS", "type": "SUCCESS"}},
                "setup_duration": 240000 if run.get("classic") else 15000, "execution_duration": 30000,
                "tasks": [{"task_key": run["task_key"], "run_id": int(run_id or 0) + 1,
                           "state": {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"}}]}, None

    def _jobs__get_run_output(self, run_id: int = 0, **_):
        run = self.runs.get(int(run_id or 0) - 1, {})
        cfg = run.get("config") or {}
        host = (cfg.get("workspace_host") or "demo.cloud.databricks.com").replace("https://", "").strip("/")
        network = [{"host": host, "port": 443, "ok": True, "addresses": ["10.0.12.34"], "ms": 12}]
        if run.get("classic"):
            network += [{"host": "s3.us-west-2.amazonaws.com", "port": 443, "ok": True,
                         "addresses": ["10.0.40.7"], "ms": 9},
                        {"host": "sts.us-west-2.amazonaws.com", "port": 443, "ok": True,
                         "addresses": ["10.0.40.8"], "ms": 8}]
        for item in cfg.get("probe_hosts") or []:
            h, _, p = str(item).partition(":")
            network.append({"host": h, "port": int(p or 443), "ok": True, "addresses": ["10.0.50.2"], "ms": 11})
        nonce = cfg.get("nonce", "")
        out = {
            "compute": cfg.get("compute"), "network": network,
            "context": {"workspace_host": host, "region": "us-west-2", "cloud": "aws" if run.get("classic") else ""},
            "reads": {"table": {"ok": True, "rows": 1},
                      "volume": {"ok": True, "files": ["canary.txt"], "content": "canary"}},
            "write": {"ok": True, "table": cfg.get("scratch_table"), "read_back": [nonce], "matches": True},
            "egress": {"allowed": {"ok": True, "url": cfg.get("egress_allowed_url"), "status": 200}},
        }
        if cfg.get("egress_blocked_url"):
            out["egress"]["blocked"] = {"ok": False, "url": cfg["egress_blocked_url"],
                                        "error": "URLError: <urlopen error [Errno 110] Connection timed out>"}
        return {"notebook_output": {"result": json.dumps(out), "truncated": False},
                "metadata": {"run_id": run_id}}, None

    def _jobs__cancel_run(self, **_):
        return {}, None

    # -- posture ------------------------------------------------------------
    def _workspace_conf__get_status(self, keys: str = "", **_):
        return {k: _GOOD_CONF.get(k) for k in str(keys).split(",") if k}, None

    def _token_management__list(self, **_):
        expires = int((time.time() + 30 * 86400) * 1000)
        return [{"token_id": "demo-token", "created_by_username": "ci@example.com", "comment": "ci",
                 "expiry_time": expires, "creation_time": int(time.time() * 1000)}], None

    def _ip_access_lists__list(self, **_):
        return [{"list_id": "demo-allow", "label": "corporate egress", "list_type": "ALLOW", "enabled": True,
                 "ip_addresses": ["203.0.113.0/24"], "address_count": 1}], None

    def _groups__list(self, **_):
        return [{"id": "demo-admins", "display_name": "admins", "members": [
            {"value": "u1", "display": "alice@example.com", "$ref": "Users/u1"},
            {"value": "sp1", "display": "validation-platform", "$ref": "ServicePrincipals/sp1"}]}], None

    def _service_principals__list(self, **_):
        return [{"id": "sp1", "display_name": "validation-platform", "application_id": "demo-app"}], None
