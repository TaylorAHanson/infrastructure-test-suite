"""Provision the canary fixtures the seeded health Tests read, using the Databricks SDK.

Prefer the Terraform module next to this file (``main.tf``) so the same apply
that changes a workspace also owns its canaries. This script is for a quick
start, or for a workspace that isn't managed by Terraform. It is idempotent:
run it again to repair drift.

    python infra/canary/provision_canary.py --profile myenv \\
        --principal <test service principal application id> \\
        --volume-location s3://bucket/validation_canary/files \\
        [--storage-root s3://bucket/validation_canary] [--warehouse-id <id>]

It creates, in catalog ``--catalog`` (default ``validation_canary``):

* schema ``health`` with table ``ping`` (one row: 1, 'canary'), table
  ``forbidden`` (never granted to the principal), and external volume ``files``
  on ``--volume-location`` holding ``canary.txt`` ("canary");
* schema ``scratch`` where the principal may create and write tables;
* grants for the principal on all of it except ``forbidden``.

Then it prints the target tags to set in the app.
"""
from __future__ import annotations

import argparse
import io
import sys
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service import catalog as uc
from databricks.sdk.service.sql import State, StatementState


def _sql(w: WorkspaceClient, warehouse_id: str, statement: str) -> None:
    resp = w.statement_execution.execute_statement(statement=statement, warehouse_id=warehouse_id,
                                                   wait_timeout="50s")
    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(3)
        resp = w.statement_execution.get_statement(resp.statement_id)
    if resp.status and resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"{statement}\n  -> {resp.status.error.message if resp.status.error else resp.status.state}")


def _pick_warehouse(w: WorkspaceClient, chosen: str | None) -> str:
    if chosen:
        return chosen
    warehouses = sorted(w.warehouses.list(), key=lambda x: (x.state != State.RUNNING,
                                                            not x.enable_serverless_compute))
    if not warehouses:
        sys.exit("no SQL warehouse found; pass --warehouse-id")
    return warehouses[0].id


def _grant(w: WorkspaceClient, kind: uc.SecurableType, name: str, principal: str,
           privileges: list[uc.Privilege]) -> None:
    w.grants.update(securable_type=kind.value, full_name=name,
                    changes=[uc.PermissionsChange(principal=principal, add=privileges)])
    print(f"  granted {', '.join(p.value for p in privileges)} on {kind.value.lower()} {name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", help="Databricks CLI profile (default: the SDK's default auth)")
    ap.add_argument("--catalog", default="validation_canary",
                    help="created if missing; an existing catalog (e.g. the workspace catalog) works too")
    ap.add_argument("--health-schema", default="health")
    ap.add_argument("--scratch-schema", default="scratch")
    ap.add_argument("--principal", required=True,
                    help="application id of the service principal the Tests run as")
    ap.add_argument("--volume-location", required=True,
                    help="a path under a real external location, e.g. s3://bucket/validation_canary/files")
    ap.add_argument("--storage-root", help="managed location for the catalog (needed when the "
                                           "metastore has no root storage)")
    ap.add_argument("--warehouse-id", help="the warehouse used to create the tables (default: any)")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    cat = args.catalog
    warehouse = _pick_warehouse(w, args.warehouse_id)
    print(f"Provisioning canary fixtures in {w.config.host} (catalog {cat}, warehouse {warehouse})")

    try:
        w.catalogs.get(cat)
        print(f"  catalog {cat} exists")
    except NotFound:
        w.catalogs.create(name=cat, storage_root=args.storage_root,
                          comment="Canary fixtures for the validation platform's health tests")
        print(f"  created catalog {cat}")
    health, scratch = f"{cat}.{args.health_schema}", f"{cat}.{args.scratch_schema}"
    for schema in (args.health_schema, args.scratch_schema):
        try:
            w.schemas.get(f"{cat}.{schema}")
            print(f"  schema {cat}.{schema} exists")
        except NotFound:
            w.schemas.create(name=schema, catalog_name=cat)
            print(f"  created schema {cat}.{schema}")

    _sql(w, warehouse, f"CREATE TABLE IF NOT EXISTS {health}.ping (id INT, note STRING)")
    _sql(w, warehouse, f"INSERT OVERWRITE {health}.ping VALUES (1, 'canary')")
    _sql(w, warehouse, f"CREATE TABLE IF NOT EXISTS {health}.forbidden (id INT, note STRING)")
    _sql(w, warehouse, f"INSERT OVERWRITE {health}.forbidden VALUES (1, 'you should not be able to read this')")
    print(f"  wrote {health}.ping and {health}.forbidden")

    volume = f"{health}.files"
    try:
        w.volumes.read(volume)
        print(f"  volume {volume} exists")
    except NotFound:
        w.volumes.create(catalog_name=cat, schema_name=args.health_schema, name="files",
                         volume_type=uc.VolumeType.EXTERNAL, storage_location=args.volume_location)
        print(f"  created external volume {volume} on {args.volume_location}")
    volume_path = f"/Volumes/{cat}/{args.health_schema}/files"
    w.files.upload(f"{volume_path}/canary.txt", io.BytesIO(b"canary"), overwrite=True)
    print(f"  wrote {volume_path}/canary.txt")

    p = args.principal
    _grant(w, uc.SecurableType.CATALOG, cat, p, [uc.Privilege.USE_CATALOG])
    _grant(w, uc.SecurableType.SCHEMA, health, p, [uc.Privilege.USE_SCHEMA])
    _grant(w, uc.SecurableType.TABLE, f"{health}.ping", p, [uc.Privilege.SELECT])
    _grant(w, uc.SecurableType.VOLUME, volume, p, [uc.Privilege.READ_VOLUME])
    _grant(w, uc.SecurableType.SCHEMA, scratch, p,
           [uc.Privilege.USE_SCHEMA, uc.Privilege.CREATE_TABLE, uc.Privilege.SELECT, uc.Privilege.MODIFY])
    forbidden = w.grants.get_effective(securable_type=uc.SecurableType.TABLE.value,
                                       full_name=f"{health}.forbidden", principal=p)
    held = sorted({f"{a.privilege.value} (from {a.inherited_from_name or 'the table'})"
                   for pa in (forbidden.privilege_assignments or []) for a in (pa.privileges or [])
                   if a.privilege and a.privilege.value in ("SELECT", "ALL_PRIVILEGES")})
    if held:
        print(f"  WARNING: {p} can already read {health}.forbidden: {', '.join(held)}. "
              "The deny check will fail until that grant is narrowed.")

    metastore = w.metastores.current()
    print("\nSet these tags on the target in the app:")
    print(f"  canary_catalog         = {cat}")
    if args.health_schema != "health":
        print(f"  canary_schema          = {args.health_schema}")
    if args.scratch_schema != "scratch":
        print(f"  scratch_schema         = {args.scratch_schema}")
    print(f"  expected_metastore_id  = {metastore.metastore_id}")
    print(f"  warehouse_id           = {warehouse}")


if __name__ == "__main__":
    main()
