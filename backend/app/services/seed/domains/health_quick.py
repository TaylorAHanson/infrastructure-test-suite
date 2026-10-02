"""Post-apply health, quick tier: read-only Tests that run after every apply.

Each check reads or is refused a **known canary object** that the same
terraform provisions in every workspace (``docs/canary-fixtures.md``), so a
failure means the apply broke something it manages — not "the API answered".
Both Tests are R0 with no advisory checks, so both volunteer for the CI Gate
via ``ci_gate_tests``; the gate re-derives that from each script's analysis.
"""
from __future__ import annotations

from app.services.seed.contribution import Contribution, TestSpec

WORKSPACE_ACCESS = '''"""Workspace access

The target's identity authenticates, the workspace is assigned the Unity Catalog
metastore it should be, and the canary catalog is visible. A bad apply breaks
these first: an IP access list that locks out automation, a service principal
or OAuth change, a force-new metastore reassignment, a catalog binding or grant
that terraform replaced.

Target tags (or run params) this test reads:
  expected_metastore_id   the metastore this workspace must be assigned to
  canary_catalog          the catalog the canary fixtures live in (default validation_canary)
"""
CATALOG = setting("canary_catalog", "validation_canary")

with check("the target identity authenticates"):
    me = w.current_user.me()
    record("identity", me.user_name)
    assert me.active is not False, f"{me.user_name} is deactivated"

with check("the expected metastore is assigned"):
    assignment = w.metastores.current()
    record("metastore_id", assignment.metastore_id)
    record("default_catalog", assignment.default_catalog_name)
    assert assignment.metastore_id, "no Unity Catalog metastore is assigned to this workspace"
    expected = setting("expected_metastore_id")
    if expected:
        assert assignment.metastore_id == expected, (
            f"assigned to metastore {assignment.metastore_id}, expected {expected}")
    else:
        log("expected_metastore_id isn't set on this target, so only presence was checked")

with check("the canary catalog is visible"):
    catalog = w.catalogs.get(name=CATALOG)
    record("catalog", catalog.name)
    record("isolation_mode", catalog.isolation_mode)
    assert catalog.name == CATALOG, f"asked for {CATALOG}, got {catalog.name}"
'''

UC_DATA_PATH = '''"""Unity Catalog data path

Reads real data through a SQL warehouse: the known row in a managed canary
table, the known file in a volume on the workspace's external location, and a
table the identity was never granted, which must be refused. Together they
cover managed storage and its KMS key, the storage credential's IAM trust and
external ID, the bucket policy, and grants terraform manages authoritatively.

Target tags (or run params):
  canary_catalog   default validation_canary
  canary_schema    default health
  warehouse_id     the SQL warehouse to use (default: a running or serverless one)
"""
CATALOG = setting("canary_catalog", "validation_canary")
SCHEMA = f"{CATALOG}.{setting('canary_schema', 'health')}"
VOLUME = f"/Volumes/{CATALOG}/{setting('canary_schema', 'health')}/files"

with check("a SQL warehouse runs a statement"):
    rows = sql("SELECT 1 AS ok")
    assert rows and rows[0].ok == "1", f"unexpected result: {rows}"

with check("the canary table returns its known row"):
    rows = sql(f"SELECT id, note FROM {SCHEMA}.ping")
    record("rows", rows)
    assert len(rows) == 1, f"expected 1 row in {SCHEMA}.ping, got {len(rows)}"
    assert rows[0].note == "canary", f"the row's note is {rows[0].note!r}, expected 'canary'"

with check("the canary volume serves its known file"):
    files = sql(f"LIST '{VOLUME}'")
    names = [f.name for f in files]
    record("files", names)
    assert "canary.txt" in names, f"canary.txt is missing from {VOLUME} (found {names})"
    content = sql(f"SELECT value FROM read_files('{VOLUME}/canary.txt', format => 'text')")
    record("content", [r.value for r in content])
    assert [r.value for r in content] == ["canary"], f"canary.txt holds {content}, expected 'canary'"

with expect_deny("the ungranted canary table is refused"):
    sql(f"SELECT * FROM {SCHEMA}.forbidden LIMIT 1")
'''


TESTS: dict[str, TestSpec] = {
    "workspace_access": TestSpec(
        "Workspace access",
        "The target identity authenticates, the expected metastore is assigned, and the "
        "canary catalog is visible.",
        tags=["health", "identity", "uc", "quick"],
        script=WORKSPACE_ACCESS,
    ),
    "uc_data_path": TestSpec(
        "Unity Catalog data path",
        "A warehouse reads the canary table and volume file, and the ungranted canary "
        "table is refused.",
        tags=["health", "uc", "sql", "storage", "quick"],
        script=UC_DATA_PATH,
    ),
}


CONTRIBUTION = Contribution(
    tests=TESTS,
    ci_gate_tests=["workspace_access", "uc_data_path"],
)
