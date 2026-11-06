mock_provider "oci" {}
mock_provider "oci" { alias = "home" }
mock_provider "time" {}

variables {
  tenancy_ocid            = "ocid1.tenancy.oc1..fixture"
  compartment_ocid        = "ocid1.compartment.oc1..fixture"
  region                  = "us-chicago-1"
  home_region             = "us-ashburn-1"
  deployment_suffix       = "test1234"
  identity_domain_id      = "ocid1.domain.oc1..fixture"
  identity_domain_url     = "https://fixture.identity.oraclecloud.com"
  admin_user_ocid         = "ocid1.user.oc1..fixture"
  agent_model_id          = "ocid1.generativeaimodel.oc1..fixture"
  objectstorage_namespace = "fixture"
  artifacts_bucket_name   = "market-artifacts-fixture"
}

run "new_resources_are_owned" {
  command = plan
  assert {
    condition     = length(oci_ai_data_platform_ai_data_platform.market) == 1 && length(oci_objectstorage_bucket.artifacts) == 1
    error_message = "New selections must plan exactly one platform and artifacts bucket."
  }
  assert {
    condition     = output.owned_ai_data_platform && output.owned_artifacts_bucket && output.workspace_key == ""
    error_message = "New workspace is resolved after bootstrap; do not fabricate a native key."
  }
  assert {
    condition     = oci_ai_data_platform_ai_data_platform.market[0].freeform_tags.deployment_binding == output.deployment_binding && oci_objectstorage_bucket.artifacts[0].freeform_tags.deployment_binding == output.deployment_binding && output.deployment_binding == sha256(join(":", [var.tenancy_ocid, var.region, var.compartment_ocid, var.deployment_suffix, var.identity_domain_id, var.admin_user_ocid]))
    error_message = "New platform and bucket must bind to the exact tenancy, region, compartment, stable deployment suffix, domain and administrator."
  }
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
    aidp_mode                      = "existing"
    existing_ai_data_platform_ocid = "ocid1.aidataplatform.oc1..external"
    existing_workspace_key         = "external_workspace_key"
    artifacts_bucket_mode          = "existing"
  }
  assert {
    condition     = length(oci_ai_data_platform_ai_data_platform.market) == 0 && length(oci_identity_policy.aidp_service) == 0 && length(oci_objectstorage_bucket.artifacts) == 0
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
