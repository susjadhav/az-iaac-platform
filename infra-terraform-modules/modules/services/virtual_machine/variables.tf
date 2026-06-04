# infra-terraform-modules/modules/services/virtual_machine/variables.tf

variable "organization" {
  type        = string
  description = "Short organization identifier"
}

variable "project" {
  type        = string
  description = "Project or workload name"
}

variable "environment" {
  type        = string
  description = "dev | staging | prod"
}

variable "region" {
  type        = string
  description = "Azure region (e.g. uksouth)"
}

variable "resource_name" {
  type        = string
  description = "Fully resolved resource name from the platform naming convention"
}

variable "owner_email" {
  type        = string
  description = "Email of the resource owner"
}

variable "request_id" {
  type        = string
  description = "Platform request UUID — applied as a resource tag for traceability"
}

# --- VM-specific ---

variable "os_type" {
  type        = string
  description = "Linux or Windows"
  validation {
    condition     = contains(["Linux", "Windows"], var.os_type)
    error_message = "os_type must be Linux or Windows."
  }
}

variable "vm_size" {
  type        = string
  description = "Azure VM SKU (e.g. Standard_B2ms)"
  default     = "Standard_B2ms"
}

variable "os_disk_size_gb" {
  type        = number
  description = "OS disk size in GB"
  default     = 128

  validation {
    condition     = var.os_disk_size_gb >= 30 && var.os_disk_size_gb <= 4096
    error_message = "os_disk_size_gb must be between 30 and 4096."
  }
}

variable "data_disk_size_gb" {
  type        = number
  description = "Optional data disk size in GB. 0 = no data disk."
  default     = 0
}

variable "availability_zone" {
  type        = string
  description = "Availability zone (1, 2, or 3). Empty string = no zone."
  default     = ""
}

variable "enable_accelerated_networking" {
  type        = bool
  description = "Enable accelerated networking on the NIC"
  default     = false
}

variable "admin_username" {
  type        = string
  description = "Local administrator username"
  default     = "azureuser"
}

variable "ssh_public_key" {
  type        = string
  description = "SSH public key for Linux VMs. Required when os_type = Linux."
  default     = ""
  sensitive   = true
}

# --- Networking (resolved by inventory lookup before TF runs) ---

variable "vnet_resource_group_name" {
  type        = string
  description = "Resource group containing the target VNet"
  default     = ""
}

variable "vnet_name" {
  type        = string
  description = "Target VNet name"
  default     = ""
}

variable "subnet_name" {
  type        = string
  description = "Target subnet name within the VNet"
  default     = "default"
}

# --- Shared infra ---

variable "state_resource_group_name" {
  type        = string
  description = "Resource group holding the Terraform state storage account"
}

variable "state_storage_account_name" {
  type        = string
  description = "Storage account name for the Terraform backend"
}
