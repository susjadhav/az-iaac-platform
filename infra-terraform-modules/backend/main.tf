# infra-terraform-modules/backend/main.tf
#
# State Backend Bootstrap
#
# This is the ONE piece of infrastructure that must be created manually
# (or via a local terraform apply) before any other module can run.
# It provisions the centralized Azure Blob Storage account that holds
# all other Terraform state files.
#
# Run once per deployment environment:
#   terraform init
#   terraform apply -var-file=dev.tfvars
#
# After this runs, all other modules configure their backend like:
#   terraform {
#     backend "azurerm" {
#       resource_group_name  = var.state_resource_group
#       storage_account_name = var.state_storage_account
#       container_name       = "terraform-state"
#       key                  = "{org}/{project}/{env}/{service}.tfstate"
#     }
#   }

terraform {
  required_version = ">= 1.9.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
  }
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "environment" {
  type        = string
  description = "Deployment environment (dev | staging | prod)"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging, or prod."
  }
}

variable "location" {
  type        = string
  description = "Azure region for the state backend resources"
  default     = "uksouth"
}

variable "organization" {
  type        = string
  description = "Short organization identifier (lowercase, no spaces)"
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Additional tags to apply to all resources"
}

# ---------------------------------------------------------------------------
# Locals — naming convention
# ---------------------------------------------------------------------------

locals {
  prefix = "${var.organization}${var.environment}"

  common_tags = merge(
    {
      environment  = var.environment
      managed-by   = "terraform"
      component    = "iaac-platform-state-backend"
      organization = var.organization
    },
    var.tags
  )
}

# ---------------------------------------------------------------------------
# Resource Group — dedicated, locked
# ---------------------------------------------------------------------------

resource "azurerm_resource_group" "state" {
  name     = "rg-${local.prefix}-tfstate"
  location = var.location
  tags     = local.common_tags
}

# Prevent accidental deletion of the state backend RG
resource "azurerm_management_lock" "state_rg_lock" {
  name       = "lock-${local.prefix}-tfstate"
  scope      = azurerm_resource_group.state.id
  lock_level = "CanNotDelete"
  notes      = "Terraform state backend — do not delete without migration plan"
}

# ---------------------------------------------------------------------------
# Storage Account — geo-redundant, private, encrypted
# ---------------------------------------------------------------------------

# Random suffix to ensure globally unique storage account name
resource "random_id" "storage_suffix" {
  byte_length = 4
}

resource "azurerm_storage_account" "tfstate" {
  name                     = "tfstate${local.prefix}${random_id.storage_suffix.hex}"
  resource_group_name      = azurerm_resource_group.state.name
  location                 = azurerm_resource_group.state.location
  account_tier             = "Standard"
  account_replication_type = var.environment == "prod" ? "GRS" : "LRS"
  account_kind             = "StorageV2"

  # Security hardening (architecture doc §9.2)
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false  # Force AAD auth

  # Disable public network access — access via private endpoint only in prod
  public_network_access_enabled = var.environment != "prod"

  blob_properties {
    versioning_enabled       = true
    change_feed_enabled      = true
    last_access_time_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# State container with soft-delete
# ---------------------------------------------------------------------------

resource "azurerm_storage_container" "tfstate" {
  name                  = "terraform-state"
  storage_account_id    = azurerm_storage_account.tfstate.id
  container_access_type = "private"
}

# ---------------------------------------------------------------------------
# RBAC — grant the automation Service Principal access
# ---------------------------------------------------------------------------

variable "automation_service_principal_object_id" {
  type        = string
  description = "Object ID of the automation Service Principal (used by Terraform runners)"
}

resource "azurerm_role_assignment" "sp_blob_contributor" {
  scope                = azurerm_storage_account.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.automation_service_principal_object_id
}

# ---------------------------------------------------------------------------
# Diagnostic settings — send storage logs to Log Analytics
# ---------------------------------------------------------------------------

variable "log_analytics_workspace_id" {
  type        = string
  description = "Log Analytics Workspace resource ID for diagnostic logs"
  default     = ""
}

resource "azurerm_monitor_diagnostic_setting" "tfstate" {
  count = var.log_analytics_workspace_id != "" ? 1 : 0

  name                       = "diag-tfstate-${var.environment}"
  target_resource_id         = azurerm_storage_account.tfstate.id
  log_analytics_workspace_id = var.log_analytics_workspace_id

  metric {
    category = "Transaction"
    enabled  = true
  }
}

# ---------------------------------------------------------------------------
# Outputs — used to configure backend blocks in other modules
# ---------------------------------------------------------------------------

output "resource_group_name" {
  description = "Resource group containing the state backend"
  value       = azurerm_resource_group.state.name
}

output "storage_account_name" {
  description = "Storage account name — use in backend 'storage_account_name'"
  value       = azurerm_storage_account.tfstate.name
}

output "storage_account_id" {
  value = azurerm_storage_account.tfstate.id
}

output "container_name" {
  description = "Blob container name — always 'terraform-state'"
  value       = azurerm_storage_container.tfstate.name
}

output "backend_config" {
  description = "Copy-paste backend configuration block for other modules"
  value = <<-EOT
    terraform {
      backend "azurerm" {
        resource_group_name  = "${azurerm_resource_group.state.name}"
        storage_account_name = "${azurerm_storage_account.tfstate.name}"
        container_name       = "terraform-state"
        key                  = "{organization}/{project}/{environment}/{service}.tfstate"
        use_azuread_auth     = true
      }
    }
  EOT
}
