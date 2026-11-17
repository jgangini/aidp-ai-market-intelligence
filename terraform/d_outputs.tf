output "ai_data_platform_ocid" {
  value = local.platform_ocid
  precondition {
    condition     = var.aidp_mode != "existing" || (var.existing_ai_data_platform_ocid != "" && var.existing_workspace_key != "")
    error_message = "Existing AIDP requires its exact OCID and workspace key; no resource is adopted by name."
  }
  precondition {
    condition     = var.aidp_mode != "new" || (var.existing_ai_data_platform_ocid == "" && var.existing_workspace_key == "")
    error_message = "New AIDP must not carry stale existing platform or workspace selections."
  }
}

output "workspace_key" {
  description = "External key or empty for a new platform; post-apply resolves the exact native default workspace."
  value       = var.aidp_mode == "existing" ? var.existing_workspace_key : ""
}

output "workspace_name" {
  value = var.aidp_mode == "new" ? local.workspace_name : ""
}

output "artifacts_bucket_name" {
  value = var.artifacts_bucket_mode == "new" ? oci_objectstorage_bucket.artifacts[0].name : var.artifacts_bucket_name
}

output "objectstorage_namespace" {
  value = var.objectstorage_namespace
}

output "region" {
  value = var.region
}

output "compartment_id" {
  value = var.compartment_ocid
}

output "identity_domain_id" {
  value = var.identity_domain_id
}

output "identity_domain_url" {
  value = var.identity_domain_url
}

output "home_region" {
  value = var.home_region
}

output "deployment_binding" {
  value = local.deployment_binding
}

output "admin_user_ocid" {
  value = var.admin_user_ocid
}

output "agent_model_id" {
  value = var.agent_model_id
}

output "llm_auth_mode" {
  value = var.llm_auth_mode
}

output "owned_ai_data_platform" {
  value = var.aidp_mode == "new"
}

output "owned_artifacts_bucket" {
  value = var.artifacts_bucket_mode == "new"
}

output "autonomous_database_id" {
  value = local.autonomous_database_id
}

output "owned_autonomous_database" {
  value = var.autonomous_database_mode == "new"
}

output "deploy_studio_resource_labels" {
  description = "Plan-known display labels keyed by exact Terraform instance address; execution and ordering come from OCI Resource Manager."
  value = merge(
    {
      "terraform_data.memory_inputs" = "Validate agent memory configuration"
    },
    { for index, instance in oci_ai_data_platform_ai_data_platform.market : "oci_ai_data_platform_ai_data_platform.market[${index}]" => "AI Data Platform" },
    { for index, instance in oci_identity_policy.aidp_service : "oci_identity_policy.aidp_service[${index}]" => "AIDP service permissions" },
    { for index, instance in time_sleep.aidp_iam_propagation : "time_sleep.aidp_iam_propagation[${index}]" => "IAM permission propagation" },
    { for index, instance in oci_objectstorage_bucket.artifacts : "oci_objectstorage_bucket.artifacts[${index}]" => "Artifacts bucket" },
    { for index, instance in oci_database_autonomous_database.memory : "oci_database_autonomous_database.memory[${index}]" => "Agent memory database" },
    { for index, instance in data.oci_database_autonomous_database.memory : "data.oci_database_autonomous_database.memory[${index}]" => "Existing agent memory database" }
  )
}
