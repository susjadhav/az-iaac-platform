"""
Tests for the ProvisioningRequest Pydantic model.
These are pure unit tests — no Azure, no Terraform, no network.
"""

import pytest
from pydantic import ValidationError

from automation.models.request import (
    AzureRegion,
    Environment,
    LifecycleAction,
    ProvisioningRequest,
    ServiceType,
    VirtualMachineConfig,
)


BASE_PAYLOAD = {
    "request_id": "00000000-0000-0000-0000-000000000001",
    "action": "create",
    "service_type": "virtual_machine",
    "organization": "acme",
    "project": "webapp",
    "environment": "dev",
    "region": "uksouth",
    "owner_email": "dev@acme.io",
    "requestor_name": "Jane Dev",
    "configuration": {
        "os_type": "Linux",
        "vm_size": "Standard_B2ms",
        "os_disk_size_gb": 128,
        "ssh_public_key": "ssh-rsa AAAAB3NzaC1yc2E",
    },
}


class TestProvisioningRequest:
    def test_valid_create_request(self):
        req = ProvisioningRequest(**BASE_PAYLOAD)
        assert req.action == LifecycleAction.CREATE
        assert req.service_type == ServiceType.VIRTUAL_MACHINE
        assert req.environment == Environment.DEV

    def test_workspace_name_format(self):
        req = ProvisioningRequest(**BASE_PAYLOAD)
        assert req.terraform_workspace_name() == "acme-webapp-dev"

    def test_state_blob_key_format(self):
        req = ProvisioningRequest(**BASE_PAYLOAD)
        assert req.state_blob_key() == "acme/webapp/dev/virtual_machine.tfstate"

    def test_resource_name_auto_generated(self):
        req = ProvisioningRequest(**BASE_PAYLOAD)
        name = req.resource_name()
        assert "acme" in name
        assert "webapp" in name
        assert "dev" in name
        assert "vm" in name
        assert "uks" in name

    def test_resource_name_override(self):
        payload = {**BASE_PAYLOAD, "resource_name_override": "my-custom-vm"}
        req = ProvisioningRequest(**payload)
        assert req.resource_name() == "my-custom-vm"

    def test_modify_requires_resource_id(self):
        payload = {**BASE_PAYLOAD, "action": "modify"}
        with pytest.raises(ValidationError, match="azure_resource_id is required"):
            ProvisioningRequest(**payload)

    def test_delete_requires_resource_id(self):
        payload = {**BASE_PAYLOAD, "action": "delete"}
        with pytest.raises(ValidationError, match="azure_resource_id is required"):
            ProvisioningRequest(**payload)

    def test_modify_with_resource_id_valid(self):
        payload = {
            **BASE_PAYLOAD,
            "action": "modify",
            "azure_resource_id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/my-vm",
        }
        req = ProvisioningRequest(**payload)
        assert req.action == LifecycleAction.MODIFY

    def test_invalid_organization_chars(self):
        payload = {**BASE_PAYLOAD, "organization": "ACME Corp!"}
        with pytest.raises(ValidationError):
            ProvisioningRequest(**payload)

    def test_invalid_environment(self):
        payload = {**BASE_PAYLOAD, "environment": "production"}
        with pytest.raises(ValidationError):
            ProvisioningRequest(**payload)

    def test_invalid_email(self):
        payload = {**BASE_PAYLOAD, "owner_email": "not-an-email"}
        with pytest.raises(ValidationError):
            ProvisioningRequest(**payload)

    def test_validated_service_config_returns_typed_model(self):
        req = ProvisioningRequest(**BASE_PAYLOAD)
        cfg = req.validated_service_config()
        assert isinstance(cfg, VirtualMachineConfig)
        assert cfg.os_type == "Linux"
        assert cfg.vm_size == "Standard_B2ms"

    def test_linux_vm_requires_ssh_key(self):
        payload = {
            **BASE_PAYLOAD,
            "configuration": {
                "os_type": "Linux",
                "vm_size": "Standard_B2ms",
                # No ssh_public_key
            },
        }
        req = ProvisioningRequest(**payload)
        with pytest.raises(ValidationError, match="ssh_public_key is required"):
            req.validated_service_config()

    def test_windows_vm_no_ssh_key_required(self):
        payload = {
            **BASE_PAYLOAD,
            "configuration": {
                "os_type": "Windows",
                "vm_size": "Standard_B2ms",
            },
        }
        req = ProvisioningRequest(**payload)
        cfg = req.validated_service_config()
        assert isinstance(cfg, VirtualMachineConfig)
        assert cfg.os_type == "Windows"


class TestVirtualMachineConfig:
    def test_defaults(self):
        cfg = VirtualMachineConfig(os_type="Windows", vm_size="Standard_B2ms")
        assert cfg.os_disk_size_gb == 128
        assert cfg.enable_accelerated_networking is False
        assert cfg.admin_username == "azureuser"

    def test_disk_size_too_small(self):
        with pytest.raises(ValidationError):
            VirtualMachineConfig(os_type="Linux", vm_size="Standard_B2ms", os_disk_size_gb=10, ssh_public_key="ssh-rsa ...")

    def test_invalid_availability_zone(self):
        with pytest.raises(ValidationError):
            VirtualMachineConfig(
                os_type="Linux",
                vm_size="Standard_B2ms",
                availability_zone="5",  # Only 1-3 allowed
                ssh_public_key="ssh-rsa ...",
            )
