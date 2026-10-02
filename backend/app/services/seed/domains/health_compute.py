"""Post-apply health, compute tier: launch real compute and probe from inside it.

The quick tier runs from the app, which is Databricks-hosted and serverless, so
it can't see the classic data plane. These Tests launch compute in the
customer's network and run a probe *inside* it. That is the only way to catch
the most common post-change failures: cluster bootstrap timeouts, the secure
cluster connectivity relay, back-end PrivateLink, subnet exhaustion, missing
S3/STS VPC endpoints, NCC and serverless egress policy.

They're R1 (they import a probe notebook, submit one-time runs, and replace a
scratch table) and do real work only at ``full`` depth, so they run on
``sandbox`` and ``prod-for-us`` targets after network-touching applies and
nightly, never in the read-only CI Gate.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, SuiteSpec, TestSpec

#: The notebook the probe job runs. It reports; the Test script decides pass/fail.
PROBE_NOTEBOOK = '''# Databricks notebook source
# Validation platform compute probe. The "Classic compute" and "Serverless compute"
# tests write this notebook and overwrite it on every run.
import json, socket, time, urllib.error, urllib.request

cfg = json.loads(dbutils.widgets.get("config"))
out = {"compute": cfg.get("compute"), "network": [], "reads": {}, "write": None, "egress": {}}


def conf(key):
    try:
        return spark.conf.get(key)
    except Exception:
        return None


host = (conf("spark.databricks.workspaceUrl") or cfg.get("workspace_host") or "")
host = host.replace("https://", "").strip("/")
region = conf("spark.databricks.clusterUsageTags.region") or cfg.get("region")
cloud = (conf("spark.databricks.clusterUsageTags.cloudProvider") or "").lower()
out["context"] = {"workspace_host": host, "region": region, "cloud": cloud}

endpoints = [(host, 443)] if host else []
if cloud == "aws" and region:
    endpoints += [("s3." + region + ".amazonaws.com", 443), ("sts." + region + ".amazonaws.com", 443)]
for item in cfg.get("probe_hosts") or []:
    name, _, port = item.partition(":")
    endpoints.append((name.strip(), int(port or 443)))

for name, port in endpoints:
    entry = {"host": name, "port": port}
    t0 = time.time()
    try:
        entry["addresses"] = sorted({a[4][0] for a in socket.getaddrinfo(name, port, proto=socket.IPPROTO_TCP)})
        with socket.create_connection((name, port), timeout=5):
            pass
        entry["ok"] = True
    except Exception as e:
        entry["ok"] = False
        entry["error"] = type(e).__name__ + ": " + str(e)
    entry["ms"] = int((time.time() - t0) * 1000)
    out["network"].append(entry)


def attempt(fn):
    try:
        return {"ok": True, **fn()}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)[:500]}


def read_volume():
    files = [f.name for f in dbutils.fs.ls(cfg["volume"])]
    content = None
    if "canary.txt" in files:
        content = dbutils.fs.head(cfg["volume"].rstrip("/") + "/canary.txt").strip()
    return {"files": files, "content": content}


def write():
    table, nonce = cfg["scratch_table"], cfg["nonce"]
    spark.sql("CREATE OR REPLACE TABLE " + table + " (nonce STRING, written_at TIMESTAMP)")
    spark.sql("INSERT INTO " + table + " VALUES ('" + nonce + "', current_timestamp())")
    got = [r.nonce for r in spark.sql("SELECT nonce FROM " + table).collect()]
    return {"table": table, "read_back": got, "matches": got == [nonce]}


def fetch(url):
    def go():
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return {"url": url, "status": r.status}
        except urllib.error.HTTPError as e:
            return {"url": url, "status": e.code}
    return go


out["reads"]["table"] = attempt(lambda: {"rows": spark.table(cfg["table"]).count()})
out["reads"]["volume"] = attempt(read_volume)
out["write"] = attempt(write)
out["egress"]["allowed"] = attempt(fetch(cfg["egress_allowed_url"]))
if cfg.get("egress_blocked_url"):
    out["egress"]["blocked"] = attempt(fetch(cfg["egress_blocked_url"]))

dbutils.notebook.exit(json.dumps(out))
'''

_COMPUTE_TEMPLATE = '''"""__TITLE__

__ABOUT__

Mutating (R1), full depth only: it writes a probe notebook to the identity's home
folder, submits a one-time run, and the probe replaces one scratch table. All of
them are overwritten on the next run, so nothing accumulates.

Target tags (or run params):
  canary_catalog, canary_schema, scratch_schema   the canary fixtures (validation_canary / health / scratch)
__EXTRA_TAGS__  egress_allowed_url        a URL workloads must reach (default https://pypi.org/simple/)
  egress_blocked_url        a URL the firewall must block (the deny half is skipped without one)
  probe_hosts               extra host:port pairs the compute must reach, comma-separated
  compute_timeout_minutes   how long to wait for the run (default __TIMEOUT__)
"""
import base64
import json
import time

COMPUTE = "__COMPUTE__"
CATALOG = setting("canary_catalog", "validation_canary")
HEALTH = setting("canary_schema", "health")
SCRATCH = setting("scratch_schema", "scratch")
TIMEOUT_MINUTES = float(setting("compute_timeout_minutes", __TIMEOUT__))
BLOCKED_URL = setting("egress_blocked_url")

PROBE = __PROBE__
__HELPERS__

def wait_for(run_id):
    deadline = time.monotonic() + TIMEOUT_MINUTES * 60
    while True:
        run = w.jobs.get_run(run_id=run_id)
        state = run.state or {}
        if state.get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            return run
        if time.monotonic() > deadline:
            w.jobs.cancel_run(run_id=run_id)
            raise AssertionError(f"run {run_id} was still {state.get('life_cycle_state')} after "
                                 f"{TIMEOUT_MINUTES:g} minutes, so it was cancelled")
        time.sleep(15)


probe = None
with check("__LAUNCH_CHECK__"):
__PRECHECK__    me = w.current_user.me()
    folder = f"/Users/{me.user_name}/validation-platform"
    path = f"{folder}/compute_probe"
    w.workspace.mkdirs(path=folder)
    w.workspace.import_(path=path, format="SOURCE", language="PYTHON", overwrite=True,
                        content=base64.b64encode(PROBE.encode()).decode())
    config = {
        "compute": COMPUTE,
        "workspace_host": target.get("host"),
        "table": f"{CATALOG}.{HEALTH}.ping",
        "volume": f"/Volumes/{CATALOG}/{HEALTH}/files",
        "scratch_table": f"{CATALOG}.{SCRATCH}.{COMPUTE}_probe",
        "nonce": f"{COMPUTE}-{time.time_ns()}",
        "egress_allowed_url": setting("egress_allowed_url", "https://pypi.org/simple/"),
        "egress_blocked_url": BLOCKED_URL,
        "probe_hosts": [h.strip() for h in str(setting("probe_hosts", "")).split(",") if h.strip()],
    }
    task = {"task_key": "probe",
            "notebook_task": {"notebook_path": path, "base_parameters": {"config": json.dumps(config)}}}
__TASK_SETUP__
    submitted = w.jobs.submit(run_name=f"validation-platform {COMPUTE} probe", tasks=[task],
                              timeout_seconds=int(TIMEOUT_MINUTES * 60))
    record("run_id", submitted.run_id)
    run = wait_for(submitted.run_id)
    state, status = run.state or {}, run.status or {}
    record("result_state", state.get("result_state"))
    record("termination", status.get("termination_details") or state.get("state_message"))
    record("setup_seconds", round((run.setup_duration or 0) / 1000))
    record("run_page_url", run.run_page_url)
    assert state.get("result_state") == "SUCCESS", (
        f"the {COMPUTE} probe run ended {state.get('result_state') or state.get('life_cycle_state')}: "
        f"{state.get('state_message') or (status.get('termination_details') or {}).get('message') or 'no message'}")
    task_run = (run.tasks or [{}])[0]
    output = w.jobs.get_run_output(run_id=task_run.run_id)
    probe = json.loads((output.notebook_output or {}).get("result") or "{}")
    assert probe, "the probe finished without reporting anything"


def need_probe():
    if probe is None:
        skip("the probe didn't run; see the launch check")


with check("__NETWORK_CHECK__"):
    need_probe()
    record("endpoints", probe["network"])
    failed = [f"{e['host']}:{e['port']} ({e.get('error')})" for e in probe["network"] if not e.get("ok")]
    assert probe["network"], "the probe had no endpoints to test"
    assert not failed, "unreachable from the cluster: " + "; ".join(failed)

with check("__READ_CHECK__"):
    need_probe()
    table, volume = probe["reads"]["table"], probe["reads"]["volume"]
    record("table", table)
    record("volume", volume)
    assert table.get("ok"), f"reading {CATALOG}.{HEALTH}.ping failed: {table.get('error')}"
    assert table.get("rows") == 1, f"{CATALOG}.{HEALTH}.ping has {table.get('rows')} rows, expected 1"
    assert volume.get("ok"), f"listing the canary volume failed: {volume.get('error')}"
    assert volume.get("content") == "canary", f"canary.txt read back as {volume.get('content')!r}"

with check("__WRITE_CHECK__"):
    need_probe()
    write = probe["write"] or {}
    record("write", write)
    assert write.get("ok"), f"writing {write.get('table') or 'the scratch table'} failed: {write.get('error')}"
    assert write.get("matches"), f"wrote one row but read back {write.get('read_back')}"

with check("__EGRESS_OK_CHECK__"):
    need_probe()
    allowed = probe["egress"]["allowed"]
    record("allowed", allowed)
    assert allowed.get("ok"), f"couldn't reach {allowed.get('url') or 'the allowed URL'}: {allowed.get('error')}"

with check("__EGRESS_DENY_CHECK__"):
    if not BLOCKED_URL:
        skip("set an egress_blocked_url tag on the target to test that the firewall blocks it")
    need_probe()
    blocked = probe["egress"].get("blocked") or {}
    record("blocked", blocked)
    assert not blocked.get("ok"), (
        f"{BLOCKED_URL} answered HTTP {blocked.get('status')} from the cluster; egress isn't restricted")
'''

_CLASSIC_HELPERS = '''
PREFERRED_NODES = ["m5d.xlarge", "m5.xlarge", "i3.xlarge", "Standard_D4ds_v5", "Standard_DS3_v2",
                   "n2-standard-4"]


def pick_node_type():
    chosen = setting("node_type_id")
    if chosen:
        return chosen
    nodes = [n for n in (w.clusters.list_node_types().node_types or [])
             if not n.get("is_deprecated") and not n.get("is_hidden") and not n.get("num_gpus")]
    by_id = {n["node_type_id"]: n for n in nodes}
    for node_id in PREFERRED_NODES:
        if node_id in by_id:
            return node_id
    sized = sorted((n for n in nodes if (n.get("num_cores") or 0) >= 4),
                   key=lambda n: (n.get("num_cores") or 0, n.get("memory_mb") or 0))
    assert sized, "no usable node type is available on this workspace"
    return sized[0]["node_type_id"]


def pick_runtime():
    chosen = setting("spark_version")
    if chosen:
        return chosen
    lts = []
    for v in w.clusters.spark_versions().versions or []:
        key, name = v.get("key") or "", v.get("name") or ""
        if "LTS" not in name or any(s in key for s in ("-ml-", "gpu", "photon", "aarch64", "apache-spark")):
            continue
        major, _, minor = key.split(".x")[0].partition(".")
        if major.isdigit() and minor.isdigit():
            lts.append(((int(major), int(minor)), key))
    assert lts, "no LTS Databricks Runtime is listed on this workspace"
    return max(lts)[1]
'''

_CLASSIC_TASK = '''    cluster = {
        "spark_version": pick_runtime(),
        "node_type_id": pick_node_type(),
        "num_workers": 0,
        "spark_conf": {"spark.databricks.cluster.profile": "singleNode", "spark.master": "local[*]"},
        "custom_tags": {"ResourceClass": "SingleNode", "validation-platform": "compute-probe"},
        "data_security_mode": "SINGLE_USER",
        "single_user_name": me.user_name,
    }
    if setting("cluster_policy_id"):
        cluster.update(policy_id=setting("cluster_policy_id"), apply_policy_default_values=True)
    record("cluster", {k: cluster[k] for k in ("spark_version", "node_type_id")})
    task["new_cluster"] = cluster
'''


def _compute_script(*, compute: str, title: str, about: str, extra_tags: str, helpers: str,
                    task_setup: str, timeout: int, names: dict[str, str], precheck: str = "") -> str:
    text = _COMPUTE_TEMPLATE
    for key, value in {
        "__TITLE__": title, "__ABOUT__": about, "__EXTRA_TAGS__": extra_tags, "__COMPUTE__": compute,
        "__TIMEOUT__": str(timeout), "__HELPERS__": helpers, "__TASK_SETUP__": task_setup,
        "__PRECHECK__": precheck,
        **{f"__{k}__": v for k, v in names.items()},
    }.items():
        text = text.replace(key, value)
    return text.replace("__PROBE__", "'''" + PROBE_NOTEBOOK.replace("'''", '"""') + "'''")


CLASSIC_COMPUTE = _compute_script(
    compute="classic",
    title="Classic compute: launch, reach, read, write",
    about=("Starts a single-node classic job cluster in the workspace's own network and runs a\n"
           "probe inside it: DNS and TCP to the workspace and the cloud's storage and STS\n"
           "endpoints, a read of the canary table and volume, a scratch-table write, and an\n"
           "egress pair. A launch failure records the run's termination reason, e.g.\n"
           "BOOTSTRAP_TIMEOUT or AWS_INSUFFICIENT_FREE_ADDRESSES_IN_SUBNET_FAILURE."),
    extra_tags=("  serverless_only           \"true\" on a workspace with no classic compute: skips this test\n"
                "  node_type_id              default: a small general-purpose node type\n"
                "  spark_version             default: the newest LTS runtime\n"
                "  cluster_policy_id         launch under this policy (recommended)\n"),
    helpers=_CLASSIC_HELPERS,
    task_setup=_CLASSIC_TASK,
    precheck=('    if str(setting("serverless_only", "")).lower() == "true":\n'
              '        skip("the target is tagged serverless_only, so there is no classic compute to test")\n'),
    timeout=20,
    names={"LAUNCH_CHECK": "a classic job cluster launches and runs the probe",
           "NETWORK_CHECK": "the cluster reaches the workspace and cloud endpoints",
           "READ_CHECK": "the cluster reads the canary table and volume",
           "WRITE_CHECK": "the cluster writes to the scratch schema",
           "EGRESS_OK_CHECK": "the cluster reaches an allowed egress URL",
           "EGRESS_DENY_CHECK": "the cluster is blocked from a non-allowlisted URL"},
)

SERVERLESS_COMPUTE = _compute_script(
    compute="serverless",
    title="Serverless compute: run, reach, read, write",
    about=("Runs the same probe as a serverless job: covers the network connectivity\n"
           "configuration (NCC), private endpoint rules and the serverless egress policy.\n"
           "Network-policy changes can take about ten minutes to reach serverless compute,\n"
           "so a failure right after an apply is worth one retry."),
    extra_tags="",
    helpers="",
    task_setup="",
    timeout=15,
    names={"LAUNCH_CHECK": "a serverless job runs the probe",
           "NETWORK_CHECK": "serverless compute reaches the workspace and private endpoints",
           "READ_CHECK": "serverless compute reads the canary table and volume",
           "WRITE_CHECK": "serverless compute writes to the scratch schema",
           "EGRESS_OK_CHECK": "serverless compute reaches an allowed egress URL",
           "EGRESS_DENY_CHECK": "serverless compute is blocked from a non-allowlisted URL"},
)

WAREHOUSE_COLD_START = '''"""SQL warehouse cold start

Starts the workspace's classic (or pro) SQL warehouse from stopped and runs a
statement on it. Classic warehouses run in the customer's network, so this
catches what a serverless warehouse can't. Skipped unless the target has a
classic_warehouse_id tag (serverless-only workspaces don't need it). The
warehouse is left to auto-stop.

Mutating (R1) when the warehouse is stopped, since starting it is a write.

Target tags (or run params):
  classic_warehouse_id         the warehouse to start
  warehouse_start_timeout_minutes   default 10
"""
import time

WAREHOUSE = setting("classic_warehouse_id")
TIMEOUT_MINUTES = float(setting("warehouse_start_timeout_minutes", 10))

with check("the classic SQL warehouse starts"):
    if not WAREHOUSE:
        skip("set a classic_warehouse_id tag on the target to test a classic warehouse")
    warehouse = w.warehouses.get(id=WAREHOUSE)
    record("initial_state", warehouse.state)
    started = time.monotonic()
    if warehouse.state in ("STOPPED", "STOPPING"):
        w.warehouses.start(id=WAREHOUSE)
    while warehouse.state != "RUNNING":
        assert warehouse.state not in ("DELETED", "DELETING"), f"warehouse {WAREHOUSE} is {warehouse.state}"
        assert time.monotonic() - started < TIMEOUT_MINUTES * 60, (
            f"warehouse {WAREHOUSE} was still {warehouse.state} after {TIMEOUT_MINUTES:g} minutes")
        time.sleep(10)
        warehouse = w.warehouses.get(id=WAREHOUSE)
    record("start_seconds", round(time.monotonic() - started))
    record("health", warehouse.health)

with check("the classic SQL warehouse runs a statement"):
    if not WAREHOUSE:
        skip("set a classic_warehouse_id tag on the target to test a classic warehouse")
    rows = sql("SELECT 1 AS ok", warehouse_id=WAREHOUSE)
    assert rows and rows[0].ok == "1", f"unexpected result: {rows}"
'''

UC_WRITE_ROUND_TRIP = '''"""Unity Catalog write round-trip

Replaces a table in the canary scratch schema, writes one row and reads it back
through a SQL warehouse. Proves the write path the quick tier can't: the
managed location's permissions, encryption with the workspace's KMS key, and
CREATE/MODIFY grants on the scratch schema.

Mutating (R1): one table, replaced on every run, so nothing accumulates.

Target tags (or run params):
  canary_catalog   default validation_canary
  scratch_schema   default scratch
  warehouse_id     the SQL warehouse to use (default: a running or serverless one)
"""
import time

TABLE = f"{setting('canary_catalog', 'validation_canary')}.{setting('scratch_schema', 'scratch')}.write_roundtrip"

with check("a row written to the scratch schema reads back"):
    nonce = f"roundtrip-{time.time_ns()}"
    sql(f"CREATE OR REPLACE TABLE {TABLE} (nonce STRING, written_at TIMESTAMP)")
    sql(f"INSERT INTO {TABLE} VALUES ('{nonce}', current_timestamp())")
    rows = sql(f"SELECT nonce FROM {TABLE}")
    record("table", TABLE)
    record("read_back", [r.nonce for r in rows])
    assert [r.nonce for r in rows] == [nonce], f"wrote {nonce!r}, read back {[r.nonce for r in rows]}"
'''


TESTS: dict[str, TestSpec] = {
    "classic_compute": TestSpec(
        "Classic compute",
        "A single-node job cluster launches in the workspace's network and, from inside it, "
        "reaches the workspace and cloud endpoints, reads and writes the canary objects, and "
        "gets the expected egress.",
        tags=["health", "compute", "network", "classic", "full"],
        script=CLASSIC_COMPUTE,
    ),
    "serverless_compute": TestSpec(
        "Serverless compute",
        "A serverless job runs the same probe: NCC, private endpoints, egress policy, and "
        "canary reads and writes.",
        tags=["health", "compute", "network", "serverless", "full"],
        script=SERVERLESS_COMPUTE,
    ),
    "warehouse_cold_start": TestSpec(
        "SQL warehouse cold start",
        "The classic SQL warehouse starts from stopped and runs a statement.",
        tags=["health", "sql", "classic", "full"],
        script=WAREHOUSE_COLD_START,
    ),
    "uc_write_round_trip": TestSpec(
        "Unity Catalog write round-trip",
        "A row written to the canary scratch schema reads back.",
        tags=["health", "uc", "sql", "storage", "full"],
        script=UC_WRITE_ROUND_TRIP,
    ),
}


SUITES: list[SuiteSpec] = [
    SuiteSpec(
        "Post-apply health: compute & network",
        "Launches real compute and probes from inside the customer's network. Run at full "
        "depth on sandbox or prod-for-us targets after network-touching applies, and nightly.",
        ["classic_compute", "serverless_compute", "warehouse_cold_start", "uc_write_round_trip"],
        tags=["seeded", "health", "compute"],
    ),
]


CONTRIBUTION = Contribution(
    tests=TESTS,
    suites=SUITES,
)
