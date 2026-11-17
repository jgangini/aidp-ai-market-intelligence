variable "tenancy_ocid" {
  type        = string
  description = "Tenancy of the authorized operator; provided by Deploy Studio."
}

variable "compartment_ocid" {
  type        = string
  description = "Target compartment resolved or created by Deploy Studio."
}

variable "region" {
  type = string
}

variable "home_region" {
  type        = string
  description = "Tenancy home region resolved by preflight for IAM policy writes."
}

variable "deployment_suffix" {
  type        = string
  description = "Stable installation suffix supplied by Deploy Studio."
  validation {
    condition     = can(regex("^[a-z0-9]{4,12}$", var.deployment_suffix))
    error_message = "deployment_suffix must contain 4-12 lowercase letters or digits."
  }
}

variable "aidp_mode" {
  type    = string
  default = "new"
  validation {
    condition     = contains(["new", "existing"], var.aidp_mode)
    error_message = "aidp_mode must be new or existing."
  }
}

variable "new_aidp_name" {
  type        = string
  default     = "aidp_market_intelligence"
  description = "Display name for a new AIDP; existing platforms remain bound by OCID."
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._ -]{0,254}$", var.new_aidp_name))
    error_message = "new_aidp_name must contain 1-255 characters, starting with a letter or digit and using letters, digits, spaces, dots, underscores or hyphens."
  }
}

variable "existing_ai_data_platform_ocid" {
  type    = string
  default = ""
  validation {
    condition     = var.existing_ai_data_platform_ocid == "" || can(regex("^ocid1\\.aidataplatform\\.[^[:space:]]+$", var.existing_ai_data_platform_ocid))
    error_message = "An exact AIDP OCID is required for an external platform."
  }
}

variable "existing_workspace_key" {
  type    = string
  default = ""
  validation {
    condition     = var.existing_workspace_key == "" || can(regex("^[A-Za-z0-9_-]{1,128}$", var.existing_workspace_key))
    error_message = "existing_workspace_key must be the exact native key, not a display name or path."
  }
}

variable "identity_domain_id" {
  type = string
  validation {
    condition     = can(regex("^ocid1\\.domain\\.[^[:space:]]+$", var.identity_domain_id))
    error_message = "identity_domain_id must identify the selected Identity Domain."
  }
}

variable "identity_domain_url" {
  type        = string
  description = "Canonical URL read from the selected OCI domain by preflight, never a browser-supplied endpoint."
  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+/?$", var.identity_domain_url))
    error_message = "identity_domain_url must be a canonical HTTPS domain endpoint."
  }
}

variable "admin_user_ocid" {
  type        = string
  description = "Selected eligible resource owner; never replaces the operator signer."
  validation {
    condition     = can(regex("^ocid1\\.user\\.[^[:space:]]+$", var.admin_user_ocid))
    error_message = "admin_user_ocid must be a stable OCI user OCID."
  }
}

variable "agent_model_id" {
  type        = string
  description = "OCI model selected by Deploy Studio and checked by preflight."
  validation {
    condition     = length(trimspace(var.agent_model_id)) > 0
    error_message = "Select a model; installation does not enable research."
  }
}

variable "llm_auth_mode" {
  type        = string
  default     = "oci_iam"
  description = "OCI inference authentication: IAM or OpenAI-compatible API key; the key stays outside Terraform."
  validation {
    condition     = contains(["oci_iam", "openai"], var.llm_auth_mode)
    error_message = "Select oci_iam or openai."
  }
}

variable "objectstorage_namespace" {
  type = string
}

variable "artifacts_bucket_mode" {
  type    = string
  default = "new"
  validation {
    condition     = contains(["new", "existing"], var.artifacts_bucket_mode)
    error_message = "artifacts_bucket_mode must be new or existing."
  }
}

variable "artifacts_bucket_name" {
  type        = string
  description = "Exact artifacts bucket name, selected or created; no medallion buckets are invented."
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]{1,256}$", var.artifacts_bucket_name))
    error_message = "artifacts_bucket_name must be a valid explicit bucket name."
  }
}

variable "lifecycle_action" {
  type        = string
  default     = "install"
  description = "Hook action; does not replace infrastructure."
  validation {
    condition     = contains(["install", "update", "redeploy", "rollback"], var.lifecycle_action)
    error_message = "Choose an explicit supported lifecycle action."
  }
}

variable "operation_request_id" {
  type        = string
  default     = ""
  description = "Optional stable UUID for a new maintenance request; never a credential."
  validation {
    condition     = var.operation_request_id == "" || can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.operation_request_id))
    error_message = "A canonical request UUID is required."
  }
}

variable "test_session_id" {
  type        = string
  default     = ""
  description = "Optional existing bound TEST session; no session reset."
}

variable "autonomous_database_mode" {
  type    = string
  default = "new"
  validation {
    condition     = contains(["new", "existing"], var.autonomous_database_mode)
    error_message = "Choose new or existing memory database."
  }
}

variable "autonomous_database_version" {
  type    = string
  default = "26ai"
  validation {
    condition     = var.autonomous_database_version == "26ai"
    error_message = "Agent Flow memory requires Oracle AI Database 26ai."
  }
}

variable "autonomous_database_workload" {
  type    = string
  default = "DW"
  validation {
    condition     = var.autonomous_database_workload == "DW"
    error_message = "Agent Flow memory requires Autonomous AI Lakehouse (DW)."
  }
}

variable "autonomous_database_compute_count" {
  type    = number
  default = 4
  validation {
    condition     = var.autonomous_database_compute_count == floor(var.autonomous_database_compute_count) && var.autonomous_database_compute_count >= 2 && var.autonomous_database_compute_count <= 512
    error_message = "Choose an integer ECPU count from 2 to 512."
  }
}

variable "existing_autonomous_database_ocid" {
  type    = string
  default = ""
  validation {
    condition     = var.existing_autonomous_database_ocid == "" || can(regex("^ocid1\\.autonomousdatabase\\.[^[:space:]]+$", var.existing_autonomous_database_ocid))
    error_message = "Select the exact existing Autonomous database OCID."
  }
}

variable "autonomous_database_admin_password" {
  type        = string
  sensitive   = true
  default     = ""
  description = "ADMIN password for database creation; protected hook input enables AI. Never sent to the AIDP Terraform resource."
  validation {
    condition = var.autonomous_database_admin_password == "" || (
      length(var.autonomous_database_admin_password) >= 12 && length(var.autonomous_database_admin_password) <= 30 &&
      can(regex("[A-Z]", var.autonomous_database_admin_password)) && can(regex("[a-z]", var.autonomous_database_admin_password)) &&
      can(regex("[0-9]", var.autonomous_database_admin_password)) && !strcontains(lower(var.autonomous_database_admin_password), "admin") &&
      !strcontains(var.autonomous_database_admin_password, "\"") && !strcontains(var.autonomous_database_admin_password, "'")
    )
    error_message = "ADMIN password must contain 12-30 characters, upper/lowercase and a digit, without quotes or admin."
  }
}
