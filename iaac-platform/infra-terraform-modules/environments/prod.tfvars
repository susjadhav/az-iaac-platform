# infra-terraform-modules/environments/prod.tfvars
#
# Production environment defaults.
# GRS storage, Premium disks, zone redundancy, existing hub VNet.

environment = "prod"
region      = "uksouth"

state_resource_group_name  = "rg-iaac-prod-tfstate"
state_storage_account_name = "tfstateiaacprod"

# Production uses an existing hub VNet (provisioned by global networking module)
vnet_name                = "vnet-hub-prod-uks"
vnet_resource_group_name = "rg-networking-prod"
subnet_name              = "snet-workloads"
