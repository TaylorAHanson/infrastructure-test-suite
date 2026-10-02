# Canary fixtures for the validation platform's seeded health tests.
#
# Use this module in the same Terraform that manages each workspace, so a canary
# failure after an apply means the apply broke something it owns. See
# docs/canary-fixtures.md. provision_canary.py next to this file does the same
# thing once, with the Databricks SDK, for a workspace Terraform doesn't manage.
#
# Grants use databricks_grant (one principal, additive), not databricks_grants:
# the plural resource is authoritative and would wipe every other grant on an
# existing catalog.

terraform {
  required_providers {
    databricks = { source = "databricks/databricks", version = ">= 1.50.0" }
  }
}

variable "catalog_name" {
  type        = string
  default     = "validation_canary"
  description = "Catalog for the fixtures. Set create_catalog = false to use an existing one."
}

variable "create_catalog" {
  type    = bool
  default = true
}

variable "storage_root" {
  type        = string
  default     = null
  description = "Managed location for a new catalog (needed when the metastore has no root storage)."
}

variable "health_schema" {
  type    = string
  default = "health"
}

variable "scratch_schema" {
  type    = string
  default = "scratch"
}

variable "volume_location" {
  type        = string
  description = "A path under the workspace's real external location, e.g. s3://bucket/validation_canary/files."
}

variable "test_principal" {
  type        = string
  description = "Application id of the service principal the tests run as."
}

variable "warehouse_id" {
  type        = string
  description = "SQL warehouse used to create the tables and write their rows."
}

resource "databricks_catalog" "canary" {
  count        = var.create_catalog ? 1 : 0
  name         = var.catalog_name
  storage_root = var.storage_root
  comment      = "Canary fixtures for the validation platform's health tests"
}

locals {
  catalog = var.create_catalog ? databricks_catalog.canary[0].name : var.catalog_name
}

resource "databricks_schema" "health" {
  catalog_name = local.catalog
  name         = var.health_schema
}

resource "databricks_schema" "scratch" {
  catalog_name = local.catalog
  name         = var.scratch_schema
}

resource "databricks_sql_table" "ping" {
  catalog_name = local.catalog
  schema_name  = databricks_schema.health.name
  name         = "ping"
  table_type   = "MANAGED"
  warehouse_id = var.warehouse_id
  column {
    name = "id"
    type = "INT"
  }
  column {
    name = "note"
    type = "STRING"
  }
}

resource "databricks_sql_table" "forbidden" {
  catalog_name = local.catalog
  schema_name  = databricks_schema.health.name
  name         = "forbidden"
  table_type   = "MANAGED"
  warehouse_id = var.warehouse_id
  comment      = "Never granted to the test principal: the deny check must be refused here."
  column {
    name = "id"
    type = "INT"
  }
  column {
    name = "note"
    type = "STRING"
  }
}

# Terraform can create tables but not write rows, so the rows go in through the
# SQL statement API. Needs the Databricks CLI authenticated where `apply` runs.
resource "terraform_data" "rows" {
  triggers_replace = [databricks_sql_table.ping.id, databricks_sql_table.forbidden.id]
  provisioner "local-exec" {
    command = <<-EOT
      databricks api post /api/2.0/sql/statements --json '{"warehouse_id": "${var.warehouse_id}", "wait_timeout": "50s", "statement": "INSERT OVERWRITE ${databricks_sql_table.ping.id} VALUES (1, \"canary\")"}'
      databricks api post /api/2.0/sql/statements --json '{"warehouse_id": "${var.warehouse_id}", "wait_timeout": "50s", "statement": "INSERT OVERWRITE ${databricks_sql_table.forbidden.id} VALUES (1, \"you should not be able to read this\")"}'
    EOT
  }
}

resource "databricks_volume" "files" {
  catalog_name     = local.catalog
  schema_name      = databricks_schema.health.name
  name             = "files"
  volume_type      = "EXTERNAL"
  storage_location = var.volume_location
}

resource "databricks_file" "canary" {
  path           = "${databricks_volume.files.volume_path}/canary.txt"
  content_base64 = base64encode("canary")
}

resource "databricks_grant" "catalog" {
  catalog    = local.catalog
  principal  = var.test_principal
  privileges = ["USE_CATALOG"]
}

resource "databricks_grant" "health" {
  schema     = databricks_schema.health.id
  principal  = var.test_principal
  privileges = ["USE_SCHEMA"]
}

resource "databricks_grant" "ping" {
  table      = databricks_sql_table.ping.id
  principal  = var.test_principal
  privileges = ["SELECT"]
}

resource "databricks_grant" "files" {
  volume     = databricks_volume.files.id
  principal  = var.test_principal
  privileges = ["READ_VOLUME"]
}

resource "databricks_grant" "scratch" {
  schema     = databricks_schema.scratch.id
  principal  = var.test_principal
  privileges = ["USE_SCHEMA", "CREATE_TABLE", "SELECT", "MODIFY"]
}

data "databricks_current_metastore" "this" {}

output "target_tags" {
  description = "Tags to set on this workspace's Target in the validation platform."
  value = {
    canary_catalog        = local.catalog
    canary_schema         = var.health_schema
    scratch_schema        = var.scratch_schema
    expected_metastore_id = data.databricks_current_metastore.this.id
    warehouse_id          = var.warehouse_id
  }
}
