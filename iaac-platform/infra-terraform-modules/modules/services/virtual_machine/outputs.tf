# infra-terraform-modules/modules/services/virtual_machine/outputs.tf
#
# Outputs are parsed by the Python automation layer (automation/terraform/apply.py)
# to extract the azure_resource_id and other values for Cosmos DB inventory update.
#
# IMPORTANT: Never output sensitive values (passwords, keys).
# Sensitive data goes directly to Key Vault, not Terraform outputs.

output "resource_id" {
  description = "Full Azure ARM resource ID of the provisioned VM"
  value = var.os_type == "Linux" ? (
    length(azurerm_linux_virtual_machine.vm) > 0 ? azurerm_linux_virtual_machine.vm[0].id : ""
  ) : (
    length(azurerm_windows_virtual_machine.vm) > 0 ? azurerm_windows_virtual_machine.vm[0].id : ""
  )
}

output "resource_name" {
  description = "Azure resource name of the VM"
  value       = var.resource_name
}

output "resource_group_name" {
  description = "Resource group containing the VM"
  value       = azurerm_resource_group.vm.name
}

output "resource_group_id" {
  description = "Resource group ARM ID"
  value       = azurerm_resource_group.vm.id
}

output "private_ip_address" {
  description = "Private IP address allocated to the VM NIC"
  value       = azurerm_network_interface.vm.private_ip_address
}

output "principal_id" {
  description = "System-assigned managed identity principal ID"
  value = var.os_type == "Linux" ? (
    length(azurerm_linux_virtual_machine.vm) > 0 ? azurerm_linux_virtual_machine.vm[0].identity[0].principal_id : ""
  ) : (
    length(azurerm_windows_virtual_machine.vm) > 0 ? azurerm_windows_virtual_machine.vm[0].identity[0].principal_id : ""
  )
}

output "nic_id" {
  description = "Network interface ARM ID (used by integration modules)"
  value       = azurerm_network_interface.vm.id
}

output "subnet_id" {
  description = "Subnet ARM ID the VM NIC is attached to"
  value       = local.subnet_id
}
