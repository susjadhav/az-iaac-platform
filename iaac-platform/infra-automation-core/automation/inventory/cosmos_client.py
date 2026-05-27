"""
Azure Cosmos DB client for the infrastructure inventory.

Handles all reads and writes to the inventory container.
Uses the azure-cosmos SDK with DefaultAzureCredential (managed identity / az login).

Container configuration (matches architecture doc):
  - Partition key : /project
  - Secondary indexes: organization, environment, resource_type, status, owner_email
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from azure.cosmos import CosmosClient, PartitionKey, exceptions
from azure.identity import DefaultAzureCredential

from automation.models.inventory import InventoryRecord, LastAction, ResourceStatus
from automation.utils.logger import get_logger

log = get_logger("inventory.cosmos_client")


class InventoryClient:
    """
    Thread-safe Cosmos DB client for the resource inventory.

    All write operations use upsert semantics so they are idempotent
    and safe to retry if the pipeline runner restarts.
    """

    CONTAINER_NAME = "resource-inventory"

    def __init__(self, cosmos_endpoint: str, database_name: str) -> None:
        credential = DefaultAzureCredential()
        self._client = CosmosClient(cosmos_endpoint, credential=credential)
        self._database = self._client.get_database_client(database_name)
        self._container = self._database.get_container_client(self.CONTAINER_NAME)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def create_record(self, record: InventoryRecord) -> None:
        """
        Pre-provisioning write (Step 1 of inventory write workflow).
        Creates a record with status=provisioning.
        """
        doc = record.to_cosmos_document()
        self._container.upsert_item(doc)
        log.info(
            "Inventory record created",
            request_id=record.request_id,
            resource_name=record.resource_name,
            status=record.status.value,
        )

    def mark_active(
        self,
        request_id: str,
        project: str,
        azure_resource_id: str,
        resource_name: str,
        configuration_snapshot: dict[str, Any],
    ) -> None:
        """
        Post-provisioning update (Step 2).
        Called after successful terraform apply.
        """
        self._patch_item(
            item_id=request_id,
            partition_key=project,
            operations=[
                {"op": "set", "path": "/status", "value": ResourceStatus.ACTIVE.value},
                {"op": "set", "path": "/azure_resource_id", "value": azure_resource_id},
                {"op": "set", "path": "/resource_name", "value": resource_name},
                {"op": "set", "path": "/configuration", "value": configuration_snapshot},
                {
                    "op": "set",
                    "path": "/last_modified_at",
                    "value": datetime.now(timezone.utc).isoformat(),
                },
                {"op": "set", "path": "/error_message", "value": None},
            ],
        )
        log.info(
            "Inventory record marked active",
            request_id=request_id,
            azure_resource_id=azure_resource_id,
        )

    def mark_failed(
        self, request_id: str, project: str, error_message: str
    ) -> None:
        """Failure update — preserves the record with status=failed."""
        self._patch_item(
            item_id=request_id,
            partition_key=project,
            operations=[
                {"op": "set", "path": "/status", "value": ResourceStatus.FAILED.value},
                {"op": "set", "path": "/error_message", "value": error_message},
                {
                    "op": "set",
                    "path": "/last_modified_at",
                    "value": datetime.now(timezone.utc).isoformat(),
                },
            ],
        )
        log.warning("Inventory record marked failed", request_id=request_id)

    def mark_modifying(self, request_id: str, project: str) -> None:
        self._patch_item(
            item_id=request_id,
            partition_key=project,
            operations=[
                {"op": "set", "path": "/status", "value": ResourceStatus.MODIFYING.value},
                {"op": "set", "path": "/last_action", "value": LastAction.MODIFY.value},
            ],
        )

    def mark_modified(
        self,
        request_id: str,
        project: str,
        configuration_snapshot: dict[str, Any],
    ) -> None:
        self._patch_item(
            item_id=request_id,
            partition_key=project,
            operations=[
                {"op": "set", "path": "/status", "value": ResourceStatus.ACTIVE.value},
                {"op": "set", "path": "/configuration", "value": configuration_snapshot},
                {
                    "op": "set",
                    "path": "/last_modified_at",
                    "value": datetime.now(timezone.utc).isoformat(),
                },
            ],
        )

    def soft_delete(self, request_id: str, project: str) -> None:
        """
        Soft-delete: status → deleted, deleted_at stamped.
        Record is retained for 7-year compliance period.
        """
        self._patch_item(
            item_id=request_id,
            partition_key=project,
            operations=[
                {"op": "set", "path": "/status", "value": ResourceStatus.DELETED.value},
                {"op": "set", "path": "/last_action", "value": LastAction.DELETE.value},
                {
                    "op": "set",
                    "path": "/deleted_at",
                    "value": datetime.now(timezone.utc).isoformat(),
                },
            ],
        )
        log.info("Inventory record soft-deleted", request_id=request_id)

    def update_dependencies(
        self,
        request_id: str,
        project: str,
        dependencies: list[str],
    ) -> None:
        """Update the dependency list on a record after integration wiring."""
        self._patch_item(
            item_id=request_id,
            partition_key=project,
            operations=[
                {"op": "set", "path": "/dependencies", "value": dependencies},
            ],
        )

    def add_dependent(
        self, resource_request_id: str, project: str, dependent_arm_id: str
    ) -> None:
        """Append a new dependent to an existing resource's dependents list."""
        record = self.get_record(resource_request_id, project)
        if record is None:
            return
        updated = list(set(record.dependents + [dependent_arm_id]))
        self._patch_item(
            item_id=resource_request_id,
            partition_key=project,
            operations=[{"op": "set", "path": "/dependents", "value": updated}],
        )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_record(self, request_id: str, project: str) -> InventoryRecord | None:
        """Fetch a single record by its document ID and partition key."""
        try:
            doc = self._container.read_item(item=request_id, partition_key=project)
            return InventoryRecord.from_cosmos_document(doc)
        except exceptions.CosmosResourceNotFoundError:
            return None

    def get_active_resources_for_project(
        self, project: str, environment: str
    ) -> list[InventoryRecord]:
        """
        Integration-aware provisioning query (Section 12.1, Layer 3).
        Returns all active resources in a project+environment — used by the
        dispatcher to resolve existing resource IDs before Terraform runs.
        """
        query = (
            "SELECT * FROM c WHERE c.project = @project "
            "AND c.environment = @environment "
            "AND c.status = 'active'"
        )
        params = [
            {"name": "@project", "value": project},
            {"name": "@environment", "value": environment},
        ]
        items = list(
            self._container.query_items(
                query=query,
                parameters=params,
                partition_key=project,
            )
        )
        return [InventoryRecord.from_cosmos_document(d) for d in items]

    def get_active_dependents(self, azure_resource_id: str) -> list[InventoryRecord]:
        """
        Deletion safety check (Section 12.4).
        Returns all ACTIVE records that list azure_resource_id in their dependencies.
        If non-empty, deletion must be blocked.
        """
        query = (
            "SELECT * FROM c WHERE ARRAY_CONTAINS(c.dependencies, @arm_id) "
            "AND c.status = 'active'"
        )
        params = [{"name": "@arm_id", "value": azure_resource_id}]
        items = list(
            self._container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        return [InventoryRecord.from_cosmos_document(d) for d in items]

    def get_record_by_arm_id(self, azure_resource_id: str) -> InventoryRecord | None:
        """Look up an inventory record by its Azure ARM resource ID."""
        query = "SELECT * FROM c WHERE c.azure_resource_id = @arm_id"
        params = [{"name": "@arm_id", "value": azure_resource_id}]
        items = list(
            self._container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        if not items:
            return None
        return InventoryRecord.from_cosmos_document(items[0])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _patch_item(
        self,
        item_id: str,
        partition_key: str,
        operations: list[dict[str, Any]],
    ) -> None:
        """Apply a partial patch to a Cosmos DB document."""
        try:
            self._container.patch_item(
                item=item_id,
                partition_key=partition_key,
                patch_operations=operations,
            )
        except exceptions.CosmosResourceNotFoundError:
            log.error(
                "Patch failed — document not found",
                item_id=item_id,
                partition_key=partition_key,
            )
            raise


def get_inventory_client() -> InventoryClient:
    """Factory: reads Cosmos DB settings from environment variables."""
    endpoint = os.environ["COSMOS_ENDPOINT"]
    database = os.environ.get("COSMOS_DATABASE", "iaac-platform")
    return InventoryClient(cosmos_endpoint=endpoint, database_name=database)
