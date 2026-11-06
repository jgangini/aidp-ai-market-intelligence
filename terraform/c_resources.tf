locals {
  name_prefix        = "aidp-ai-market-intelligence-${var.deployment_suffix}"
  workspace_name     = "ai-market-intelligence-${var.deployment_suffix}"
  platform_ocid      = var.aidp_mode == "new" ? oci_ai_data_platform_ai_data_platform.market[0].id : var.existing_ai_data_platform_ocid
  deployment_binding = sha256(join(":", [var.tenancy_ocid, var.region, var.compartment_ocid, var.deployment_suffix, var.identity_domain_id, var.admin_user_ocid]))
}

# Service policy is derived from Starter Kits 3c82617, not a grant to the selected administrator.
resource "oci_identity_policy" "aidp_service" {
  count          = var.aidp_mode == "new" ? 1 : 0
  provider       = oci.home
  compartment_id = var.tenancy_ocid
  name           = "${local.name_prefix}-service"
  description    = "AIDP service access for the selected Market Intelligence compartment"
  statements = [
    "Allow any-user to {AUTHENTICATION_INSPECT, DOMAIN_INSPECT, DOMAIN_READ, DYNAMIC_GROUP_INSPECT, GROUP_INSPECT, GROUP_MEMBERSHIP_INSPECT, USER_INSPECT, USER_READ} in tenancy where all {request.principal.type='aidataplatform'}",
    "Allow any-user to manage log-groups in compartment id ${var.compartment_ocid} where all {request.principal.type='aidataplatform'}",
    "Allow any-user to read log-content in compartment id ${var.compartment_ocid} where all {request.principal.type='aidataplatform'}",
    "Allow any-user to use metrics in compartment id ${var.compartment_ocid} where all {request.principal.type='aidataplatform', target.metrics.namespace='oracle_aidataplatform'}",
    "Allow any-user to manage buckets in compartment id ${var.compartment_ocid} where all {request.principal.type='aidataplatform', any {request.permission='BUCKET_CREATE', request.permission='BUCKET_INSPECT', request.permission='BUCKET_READ', request.permission='BUCKET_UPDATE'}}",
    "Allow any-user to {TAG_NAMESPACE_USE} in tenancy where all {request.principal.type='aidataplatform'}",
    "Allow any-user to manage buckets in compartment id ${var.compartment_ocid} where all {request.principal.id=target.resource.tag.orcl-aidp.governingAidpId, any {request.permission='BUCKET_DELETE', request.permission='PAR_MANAGE', request.permission='RETENTION_RULE_LOCK', request.permission='RETENTION_RULE_MANAGE'}}",
    "Allow any-user to read objectstorage-namespaces in compartment id ${var.compartment_ocid} where all {request.principal.type='aidataplatform', request.permission='OBJECTSTORAGE_NAMESPACE_READ'}",
    "Allow any-user to manage objects in compartment id ${var.compartment_ocid} where all {request.principal.id=target.bucket.system-tag.orcl-aidp.governingAidpId}",
    "Allow any-user to use generative-ai-family in compartment id ${var.compartment_ocid} where all {request.principal.type='aidataplatform'}"
  ]
}

resource "time_sleep" "aidp_iam_propagation" {
  count           = var.aidp_mode == "new" ? 1 : 0
  create_duration = "5m"
  triggers = {
    policy_id         = oci_identity_policy.aidp_service[0].id
    policy_statements = sha256(jsonencode(oci_identity_policy.aidp_service[0].statements))
  }
}

resource "oci_ai_data_platform_ai_data_platform" "market" {
  count                  = var.aidp_mode == "new" ? 1 : 0
  compartment_id         = var.compartment_ocid
  display_name           = var.new_aidp_name
  default_workspace_name = local.workspace_name
  ai_data_platform_type  = "STRUCTURED"
  is_enable_ai_feature   = false
  freeform_tags = {
    managed-by         = "deploy-studio"
    workload           = "ai-market-intelligence"
    deployment_binding = local.deployment_binding
  }
  timeouts {
    create = "120m"
  }
  depends_on = [time_sleep.aidp_iam_propagation]
}

resource "oci_objectstorage_bucket" "artifacts" {
  count          = var.artifacts_bucket_mode == "new" ? 1 : 0
  compartment_id = var.compartment_ocid
  namespace      = var.objectstorage_namespace
  name           = var.artifacts_bucket_name
  access_type    = "NoPublicAccess"
  versioning     = "Enabled"
  freeform_tags = {
    managed-by         = "deploy-studio"
    workload           = "ai-market-intelligence"
    deployment_binding = local.deployment_binding
  }
}
