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
