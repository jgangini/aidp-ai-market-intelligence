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
