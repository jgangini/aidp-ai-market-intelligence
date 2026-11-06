terraform {
  required_version = ">= 1.7.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "= 8.28.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13"
    }
  }
}

provider "oci" {
  region = var.region
}

provider "oci" {
  alias  = "home"
  region = var.home_region
}
