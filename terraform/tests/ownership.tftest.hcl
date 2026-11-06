mock_provider "oci" {
  mock_data "oci_database_autonomous_database" {
    defaults = {
      id          = "ocid1.autonomousdatabase.oc1..memory"
      db_version  = "26ai"
      db_workload = "DW"
      state       = "AVAILABLE"
    }
  }
}
mock_provider "oci" { alias = "home" }
mock_provider "time" {}

variables {
  tenancy_ocid                       = "ocid1.tenancy.oc1..fixture"
  compartment_ocid                   = "ocid1.compartment.oc1..fixture"
  region                             = "us-chicago-1"
  home_region                        = "us-ashburn-1"
  deployment_suffix                  = "test1234"
  identity_domain_id                 = "ocid1.domain.oc1..fixture"
  identity_domain_url                = "https://fixture.identity.oraclecloud.com"
  admin_user_ocid                    = "ocid1.user.oc1..fixture"
  agent_model_id                     = "ocid1.generativeaimodel.oc1..fixture"
  objectstorage_namespace            = "fixture"
  artifacts_bucket_name              = "market-artifacts-fixture"
  autonomous_database_admin_password = "ExampleMemory123"
}

run "new_resources_are_owned" {
  command = plan
  assert {
    condition     = length(oci_database_autonomous_database.memory) == 1 && output.owned_autonomous_database && oci_database_autonomous_database.memory[0].db_version == "26ai" && oci_database_autonomous_database.memory[0].db_workload == "DW" && !oci_ai_data_platform_ai_data_platform.market[0].is_enable_ai_feature
    error_message = "Create memory storage explicitly; enable AI only through the protected post-apply hook."
  }
  assert {
    condition     = length(oci_ai_data_platform_ai_data_platform.market) == 1 && length(oci_objectstorage_bucket.artifacts) == 1
    error_message = "New selections must plan exactly one platform and artifacts bucket."
  }
  assert {
    condition     = output.owned_ai_data_platform && output.owned_artifacts_bucket && output.workspace_key == ""
    error_message = "New workspace is resolved after bootstrap; do not fabricate a native key."
  }
  assert {
    condition     = oci_ai_data_platform_ai_data_platform.market[0].display_name == "aidp_market_intelligence" && oci_database_autonomous_database.memory[0].compute_count == 4 && var.test_session_id == ""
    error_message = "The visible default name must reach the new platform while advanced memory/session defaults stay safe."
  }
  assert {
    condition     = oci_ai_data_platform_ai_data_platform.market[0].freeform_tags.deployment_binding == output.deployment_binding && oci_objectstorage_bucket.artifacts[0].freeform_tags.deployment_binding == output.deployment_binding && output.deployment_binding == sha256(join(":", [var.tenancy_ocid, var.region, var.compartment_ocid, var.deployment_suffix, var.identity_domain_id, var.admin_user_ocid]))
    error_message = "New platform and bucket must bind to the exact tenancy, region, compartment, stable deployment suffix, domain and administrator."
  }
}

run "custom_platform_name_preserves_native_bindings" {
  command = plan
  variables { new_aidp_name = "Market Research 2026" }
  assert {
    condition     = oci_ai_data_platform_ai_data_platform.market[0].display_name == "Market Research 2026" && output.workspace_name == run.new_resources_are_owned.workspace_name && output.deployment_binding == run.new_resources_are_owned.deployment_binding
    error_message = "The submitted name must name the platform without changing the workspace or ownership binding."
  }
}

run "platform_name_rejects_empty" {
  command = plan
  variables { new_aidp_name = "" }
  expect_failures = [var.new_aidp_name]
}

run "platform_name_rejects_invalid_characters" {
  command = plan
  variables { new_aidp_name = "../other/platform" }
  expect_failures = [var.new_aidp_name]
}

run "platform_name_rejects_excessive_length" {
  command = plan
  variables { new_aidp_name = join("", [for i in range(256) : "a"]) }
  expect_failures = [var.new_aidp_name]
}

run "memory_requires_creation_password" {
  command = plan
  variables { autonomous_database_admin_password = "" }
  expect_failures = [terraform_data.memory_inputs]
}

run "memory_cannot_use_old_database_version" {
  command = plan
  variables { autonomous_database_version = "19c" }
  expect_failures = [var.autonomous_database_version]
}

run "memory_rejects_fractional_compute" {
  command = plan
  variables { autonomous_database_compute_count = 2.5 }
  expect_failures = [var.autonomous_database_compute_count]
}

run "maintenance_cannot_create_memory_database" {
  command = plan
  variables { lifecycle_action = "update" }
  expect_failures = [terraform_data.memory_inputs]
}

run "new_memory_rejects_stale_external_selection" {
  command = plan
  variables { existing_autonomous_database_ocid = "ocid1.autonomousdatabase.oc1..foreign" }
  expect_failures = [terraform_data.memory_inputs]
}

run "external_memory_rejects_old_version" {
  command = plan
  variables {
    autonomous_database_mode          = "existing"
    existing_autonomous_database_ocid = "ocid1.autonomousdatabase.oc1..memory"
  }
  override_data {
    target = data.oci_database_autonomous_database.memory[0]
    values = { id = "ocid1.autonomousdatabase.oc1..memory", db_version = "19c", db_workload = "DW", state = "AVAILABLE" }
  }
  expect_failures = [data.oci_database_autonomous_database.memory]
}

run "different_administrator_changes_ownership_binding" {
  command = plan
  variables {
    admin_user_ocid = "ocid1.user.oc1..different"
  }
  assert {
    condition     = output.deployment_binding != run.new_resources_are_owned.deployment_binding
    error_message = "Changing the selected administrator must not preserve the previous installation ownership binding."
  }
}

run "external_resources_are_not_managed" {
  command = plan
  variables {
    aidp_mode                         = "existing"
    existing_ai_data_platform_ocid    = "ocid1.aidataplatform.oc1..external"
    existing_workspace_key            = "external_workspace_key"
    artifacts_bucket_mode             = "existing"
    autonomous_database_mode          = "existing"
    existing_autonomous_database_ocid = "ocid1.autonomousdatabase.oc1..memory"
  }
  assert {
    condition     = length(oci_ai_data_platform_ai_data_platform.market) == 0 && length(oci_identity_policy.aidp_service) == 0 && length(oci_objectstorage_bucket.artifacts) == 0 && length(oci_database_autonomous_database.memory) == 0 && !output.owned_autonomous_database
    error_message = "External resources and their IAM must never be imported or created by this stack."
  }
  assert {
    condition     = output.ai_data_platform_ocid == "ocid1.aidataplatform.oc1..external" && output.workspace_key == "external_workspace_key" && !output.owned_ai_data_platform && !output.owned_artifacts_bucket
    error_message = "External references must pass through exactly with external ownership."
  }
}

run "external_platform_requires_exact_workspace" {
  command = plan
  variables {
    aidp_mode                      = "existing"
    existing_ai_data_platform_ocid = "ocid1.aidataplatform.oc1..external"
  }
  expect_failures = [output.ai_data_platform_ocid]
}

run "new_platform_rejects_stale_existing_selection" {
  command = plan
  variables {
    existing_ai_data_platform_ocid = "ocid1.aidataplatform.oc1..external"
    existing_workspace_key         = "external_workspace_key"
  }
  expect_failures = [output.ai_data_platform_ocid]
}
