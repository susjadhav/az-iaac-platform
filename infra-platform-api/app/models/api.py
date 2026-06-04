"""
API-layer Pydantic models.

InboundProvisionRequest is what the UI sends — a subset of ProvisioningRequest
(the API layer adds request_id, timestamps, and pipeline_run_url).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, EmailStr

from automation.models.request import (
    AzureRegion,
    Environment,
    LifecycleAction,
    ServiceType,
)


class InboundProvisionRequest(BaseModel):
    """
    Payload received from the frontend / API consumers.
    The API layer assigns request_id before passing to ProvisioningRequest.
    """

    action: LifecycleAction
    service_type: ServiceType
    organization: str
    project: str
    environment: Environment
    region: AzureRegion
    owner_email: EmailStr
    requestor_name: str
    configuration: dict[str, Any] = {}
    resource_name_override: str | None = None
    azure_resource_id: str | None = None

    model_config = {"json_schema_extra": {
        "example": {
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
                "ssh_public_key": "ssh-rsa AAAAB3NzaC1yc...",
            },
        }
    }}


class ProvisioningAcceptedResponse(BaseModel):
    request_id: str
    status: str
    message: str
    resource_name: str | None = None
    pipeline_run_url: str | None = None
