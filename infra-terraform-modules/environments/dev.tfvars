# infra-terraform-modules/environments/dev.tfvars
#
# Development environment defaults.
# These are injected via -var-file by the Python automation layer.
# Sensitive values (subscription IDs, SP credentials) are never here —
# they are injected as ARM_* environment variables by the pipeline.

environment = "dev"
region      = "uksouth"

# State backend (populated after running backend/main.tf)
state_resource_group_name  = "rg-iaac-dev-tfstate"
state_storage_account_name = "tfstateiaacdev"   # Updated after bootstrap

# Dev-specific networking (empty = create isolated VNet per module)
vnet_name                = ""
vnet_resource_group_name = ""
subnet_name              = "default"
