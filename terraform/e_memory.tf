locals {
  autonomous_database_id = var.autonomous_database_mode == "new" ? oci_database_autonomous_database.memory[0].id : data.oci_database_autonomous_database.memory[0].id
}

resource "terraform_data" "memory_inputs" {
  lifecycle {
    precondition {
      condition     = var.autonomous_database_mode == "existing" || var.autonomous_database_admin_password != ""
      error_message = "Creating memory storage requires an ADMIN password."
    }
    precondition {
      condition     = var.autonomous_database_mode == "new" ? var.existing_autonomous_database_ocid == "" : var.existing_autonomous_database_ocid != ""
      error_message = "Choose one exact memory destination; do not carry an existing OCID into new mode."
    }
    precondition {
      condition     = var.lifecycle_action == "install" || var.autonomous_database_mode == "existing"
      error_message = "Maintenance must reuse the existing memory database."
    }
  }
}

resource "oci_database_autonomous_database" "memory" {
  count                       = var.autonomous_database_mode == "new" ? 1 : 0
  compartment_id              = var.compartment_ocid
  display_name                = "${local.name_prefix}-memory"
  db_name                     = upper(substr("MKT${var.deployment_suffix}", 0, 14))
  admin_password              = var.autonomous_database_admin_password
  db_version                  = var.autonomous_database_version
  db_workload                 = var.autonomous_database_workload
  compute_model               = "ECPU"
  compute_count               = var.autonomous_database_compute_count
  is_auto_scaling_enabled     = false
  data_storage_size_in_tbs    = 1
  license_model               = "LICENSE_INCLUDED"
  is_mtls_connection_required = true
  freeform_tags = {
    managed-by         = "deploy-studio"
    workload           = "ai-market-intelligence-memory"
    deployment_binding = local.deployment_binding
  }
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [terraform_data.memory_inputs]
}

data "oci_database_autonomous_database" "memory" {
  count                  = var.autonomous_database_mode == "existing" ? 1 : 0
  autonomous_database_id = var.existing_autonomous_database_ocid
  lifecycle {
    postcondition {
      condition     = self.id == var.existing_autonomous_database_ocid && self.db_version == "26ai" && self.db_workload == "DW" && self.state == "AVAILABLE"
      error_message = "Existing memory database must be the exact AVAILABLE 26ai DW instance."
    }
  }
}
