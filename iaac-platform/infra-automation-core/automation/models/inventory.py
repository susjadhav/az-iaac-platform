"""
Pydantic models for the Cosmos DB inventory records.

Each provisioned resource has exactly one inventory document.
The document lifecycle mirrors the resource lifecycle:
  provisioning → active → (modifying) → (deleting) → deleted
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ResourceStatus(str, Enum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    MODIFYING = "modifying"
    REFRESHING = "refreshing"
    DELETING = "deleting"
    FAILED = "failed"
    DELETED = "deleted"


class LastAction(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    REFRESH = "refresh"
    DELETE = "delete"


class InventoryRecord(BaseModel):
    """
    Represents a single resource document in Cosmos DB.

    Partition key: /project
    Document id: same as request_id (UUID)
    """

    # Cosmos DB required fields
    id: str = Field(..., description="Cosmos DB document ID = platform request ID (UUID)")
    request_id: str = Field(..., description="Originating API request ID")

    # Azure identity
    resource_name: str
    resource_type: str  # ServiceType value
    azure_resource_id: str | None = None  # Populated after successful apply

    # Organisational scope
    organization: str
    project: str  # Partition key
    environment: str
    region: str

    # Ownership
    owner_email: str
    requestor_name: str

    # Lifecycle
    status: ResourceStatus = ResourceStatus.PROVISIONING
    last_action: LastAction = LastAction.CREATE
    error_message: str | None = None

    # Terraform linkage
    terraform_workspace: str
    state_blob_key: str

    # Dependency graph
    dependencies: list[str] = Field(
        default_factory=list,
        description="azure_resource_id values this resource depends ON",
    )
    dependents: list[str] = Field(
        default_factory=list,
        description="azure_resource_id values that depend ON this resource",
    )

    # Configuration snapshot (service-specific, schema varies)
    configuration: dict[str, Any] = Field(default_factory=dict)

    # Azure tags (mirrors resource tags for cross-reference)
    tags: dict[str, str] = Field(default_factory=dict)

    # Pipeline traceability
    pipeline_run_url: str | None = None

    # Timestamps (ISO 8601, UTC)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_modified_at: datetime | None = None
    deleted_at: datetime | None = None

    def to_cosmos_document(self) -> dict[str, Any]:
        """Serialise to a dict suitable for Cosmos DB upsert."""
        data = self.model_dump(mode="json")
        # Cosmos DB uses 'id' as the partition document identifier —
        # ensure it is always a string UUID
        data["id"] = str(self.id)
        return data

    @classmethod
    def from_cosmos_document(cls, doc: dict[str, Any]) -> "InventoryRecord":
        return cls(**doc)

    def standard_tags(self) -> dict[str, str]:
        """Returns the mandatory Azure resource tags for governance."""
        return {
            "project": self.project,
            "environment": self.environment,
            "owner": self.owner_email,
            "created-by": "infra-automation-platform",
            "request-id": self.request_id,
            "organization": self.organization,
        }
