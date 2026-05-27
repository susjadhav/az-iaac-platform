# infra-terraform-modules/modules/services/virtual_machine/main.tf

terraform {
  required_version = ">= 1.9.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  backend "azurerm" {
    use_azuread_auth = true
    # resource_group_name, storage_account_name, container_name, key
    # are injected at runtime by the Python automation layer via
    # `terraform init -backend-config=...` or backend.hcl
  }
}

provider "azurerm" {
  features {
    virtual_machine {
      # Safety: don't delete OS disk when VM is destroyed via the platform.
      # The disk is managed separately to prevent accidental data loss.
      delete_os_disk_on_deletion = var.environment == "dev" ? true : false
    }
    key_vault {
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  common_tags = {
    project      = var.project
    environment  = var.environment
    owner        = var.owner_email
    created-by   = "infra-automation-platform"
    request-id   = var.request_id
    organization = var.organization
  }

  zones = var.availability_zone != "" ? [var.availability_zone] : []

  resource_group_name = "rg-${var.organization}-${var.project}-${var.environment}"
}

# ---------------------------------------------------------------------------
# Resource Group
# ---------------------------------------------------------------------------

resource "azurerm_resource_group" "vm" {
  name     = local.resource_group_name
  location = var.region
  tags     = local.common_tags
}

# ---------------------------------------------------------------------------
# Networking — use existing VNet/subnet, or create a minimal one for dev
# ---------------------------------------------------------------------------

data "azurerm_virtual_network" "target" {
  count               = var.vnet_name != "" ? 1 : 0
  name                = var.vnet_name
  resource_group_name = var.vnet_resource_group_name
}

data "azurerm_subnet" "target" {
  count                = var.vnet_name != "" ? 1 : 0
  name                 = var.subnet_name
  virtual_network_name = var.vnet_name
  resource_group_name  = var.vnet_resource_group_name
}

# Fallback: create a minimal VNet for dev/isolated deployments
resource "azurerm_virtual_network" "dev_vnet" {
  count               = var.vnet_name == "" ? 1 : 0
  name                = "vnet-${var.resource_name}"
  resource_group_name = azurerm_resource_group.vm.name
  location            = azurerm_resource_group.vm.location
  address_space       = ["10.0.0.0/24"]
  tags                = local.common_tags
}

resource "azurerm_subnet" "dev_subnet" {
  count                = var.vnet_name == "" ? 1 : 0
  name                 = "snet-vms"
  resource_group_name  = azurerm_resource_group.vm.name
  virtual_network_name = azurerm_virtual_network.dev_vnet[0].name
  address_prefixes     = ["10.0.0.0/26"]
}

locals {
  subnet_id = var.vnet_name != "" ? data.azurerm_subnet.target[0].id : azurerm_subnet.dev_subnet[0].id
}

# ---------------------------------------------------------------------------
# NIC
# ---------------------------------------------------------------------------

resource "azurerm_network_interface" "vm" {
  name                          = "nic-${var.resource_name}"
  location                      = azurerm_resource_group.vm.location
  resource_group_name           = azurerm_resource_group.vm.name
  accelerated_networking_enabled = var.enable_accelerated_networking
  tags                          = local.common_tags

  ip_configuration {
    name                          = "ipconfig1"
    subnet_id                     = local.subnet_id
    private_ip_address_allocation = "Dynamic"
  }
}

# ---------------------------------------------------------------------------
# Linux VM
# ---------------------------------------------------------------------------

resource "azurerm_linux_virtual_machine" "vm" {
  count = var.os_type == "Linux" ? 1 : 0

  name                = var.resource_name
  resource_group_name = azurerm_resource_group.vm.name
  location            = azurerm_resource_group.vm.location
  size                = var.vm_size
  admin_username      = var.admin_username
  zones               = local.zones
  tags                = local.common_tags

  network_interface_ids = [azurerm_network_interface.vm.id]

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = var.environment == "prod" ? "Premium_LRS" : "Standard_LRS"
    disk_size_gb         = var.os_disk_size_gb
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  identity {
    type = "SystemAssigned"
  }

  # Disable password authentication — SSH key only
  disable_password_authentication = true

  lifecycle {
    ignore_changes = [
      # Ignore image version drift — we pin to 'latest' at create time
      source_image_reference
    ]
  }
}

# ---------------------------------------------------------------------------
# Windows VM
# ---------------------------------------------------------------------------

resource "random_password" "windows_admin" {
  count   = var.os_type == "Windows" ? 1 : 0
  length  = 20
  special = true
}

resource "azurerm_windows_virtual_machine" "vm" {
  count = var.os_type == "Windows" ? 1 : 0

  name                = var.resource_name
  resource_group_name = azurerm_resource_group.vm.name
  location            = azurerm_resource_group.vm.location
  size                = var.vm_size
  admin_username      = var.admin_username
  admin_password      = random_password.windows_admin[0].result
  zones               = local.zones
  tags                = local.common_tags

  network_interface_ids = [azurerm_network_interface.vm.id]

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = var.environment == "prod" ? "Premium_LRS" : "Standard_LRS"
    disk_size_gb         = var.os_disk_size_gb
  }

  source_image_reference {
    publisher = "MicrosoftWindowsServer"
    offer     = "WindowsServer"
    sku       = "2022-datacenter-azure-edition"
    version   = "latest"
  }

  identity {
    type = "SystemAssigned"
  }
}

# Store Windows admin password in Key Vault (never in TF outputs)
# Key Vault must already exist (created as a global service or prior request)
# See modules/integrations/vm_keyvault_binding for the wiring pattern.

# ---------------------------------------------------------------------------
# Optional data disk
# ---------------------------------------------------------------------------

resource "azurerm_managed_disk" "data" {
  count = var.data_disk_size_gb > 0 ? 1 : 0

  name                 = "disk-${var.resource_name}-data"
  location             = azurerm_resource_group.vm.location
  resource_group_name  = azurerm_resource_group.vm.name
  storage_account_type = var.environment == "prod" ? "Premium_LRS" : "Standard_LRS"
  create_option        = "Empty"
  disk_size_gb         = var.data_disk_size_gb
  tags                 = local.common_tags
}

resource "azurerm_virtual_machine_data_disk_attachment" "data" {
  count = var.data_disk_size_gb > 0 ? 1 : 0

  managed_disk_id    = azurerm_managed_disk.data[0].id
  virtual_machine_id = var.os_type == "Linux" ? azurerm_linux_virtual_machine.vm[0].id : azurerm_windows_virtual_machine.vm[0].id
  lun                = 10
  caching            = "ReadWrite"
}
