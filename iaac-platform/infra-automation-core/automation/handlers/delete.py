"""
Refresh handler — syncs Terraform state with actual Azure resource state.
Delete handler  — tears down resources with dependency safety checks.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from automation.handlers.create import _append_log, _build_tf_vars, _run_init
from automation.inventory.cosmos_client import InventoryClient
from automation.models.inventory import ResourceStatus
from automation.models.request import ProvisioningRequest
from automation.terraform.apply import TerraformRefresh, TerraformDestroy
from automation.terraform.plan import TerraformPlan
from automation.terraform.workspace import WorkspaceManager
from automation.utils.blob_uploader import BlobUploader
from automation.utils.logger import BoundLogger


# ---------------------------------------------------------------------------
# REFRESH
# ---------------------------------------------------------------------------


def run_refresh(
    request: ProvisioningRequest,
    inventory: InventoryClient,
    uploader: BlobUploader,
    log: BoundLogger,
) -> None:
    """
    Sync Terraform state with actual Azure resource state.
    Does not create or destroy resources — only updates state file.
    """
    workspace_name = request.terraform_workspace_name()
    modules_root = Path(os.environ.get("TF_MODULES_ROOT", "/opt/infra-terraform-modules"))
    module_dir = modules_root / "modules" / "services" / request.service_type.value
    env_var_file = modules_root / "environments" / f"{request.environment.value}.tfvars"

    log.info("Starting REFRESH handler", workspace=workspace_name)

    # Mark as refreshing in inventory
    inventory._patch_item(
        item_id=request.request_id,
        partition_key=request.project,
        operations=[
            {"op": "set", "path": "/status", "value": ResourceStatus.REFRESHING.value},
        ],
    )

    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp)
        execution_log = logs_dir / "execution.log"

        try:
            _run_init(module_dir, log, execution_log)

            workspace_mgr = WorkspaceManager(module_dir, log)
            workspace_mgr.select_or_create(workspace_name)

            refresher = TerraformRefresh(module_dir, log)
            tf_vars = _build_tf_vars(request)
            refresh_result = refresher.run(
                var_file=env_var_file,
                extra_vars=tf_vars,
            )
            _append_log(execution_log, refresh_result.raw_output)

            if not refresh_result.success:
                raise RuntimeError(f"terraform refresh failed: {refresh_result.error_output}")

            # Restore to active after refresh
            inventory._patch_item(
                item_id=request.request_id,
                partition_key=request.project,
                operations=[
                    {"op": "set", "path": "/status", "value": ResourceStatus.ACTIVE.value},
                    {"op": "set", "path": "/last_action", "value": "refresh"},
                ],
            )
            uploader.upload_run_artifacts(request.request_id, logs_dir)
            log.info("REFRESH handler completed successfully")

        except Exception as exc:
            _append_log(execution_log, f"\n\nFAILED: {exc}\n")
            inventory.mark_failed(request.request_id, request.project, error_message=str(exc))
            try:
                uploader.upload_run_artifacts(request.request_id, logs_dir)
            except Exception:
                pass
            raise


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


class DependencyBlockedError(Exception):
    """Raised when a delete is attempted on a resource that has active dependents."""

    def __init__(self, resource_name: str, dependents: list[str]) -> None:
        self.resource_name = resource_name
        self.dependents = dependents
        names = ", ".join(dependents)
        super().__init__(
            f"Cannot delete '{resource_name}': it has active dependents: {names}. "
            "Remove or reassign those resources first."
        )


def run_delete(
    request: ProvisioningRequest,
    inventory: InventoryClient,
    uploader: BlobUploader,
    log: BoundLogger,
) -> None:
    """
    Tear down Azure resources.

    Safety checks performed BEFORE any Terraform command runs:
      1. Query Cosmos DB for active dependents on this resource's ARM ID.
      2. If any exist, raise DependencyBlockedError — caller must surface this
         to the user with the list of blocking resources.
    """
    workspace_name = request.terraform_workspace_name()
    modules_root = Path(os.environ.get("TF_MODULES_ROOT", "/opt/infra-terraform-modules"))
    module_dir = modules_root / "modules" / "services" / request.service_type.value
    env_var_file = modules_root / "environments" / f"{request.environment.value}.tfvars"

    log.info("Starting DELETE handler", workspace=workspace_name)

    # --- Safety check: dependency graph ---
    if request.azure_resource_id:
        active_dependents = inventory.get_active_dependents(request.azure_resource_id)
        if active_dependents:
            dependent_names = [r.resource_name for r in active_dependents]
            log.warning(
                "Delete blocked — active dependents exist",
                target_resource=request.azure_resource_id,
                dependents=dependent_names,
            )
            raise DependencyBlockedError(
                resource_name=request.resource_name_override or request.service_type.value,
                dependents=dependent_names,
            )

    # Mark as deleting
    inventory._patch_item(
        item_id=request.request_id,
        partition_key=request.project,
        operations=[
            {"op": "set", "path": "/status", "value": ResourceStatus.DELETING.value},
            {"op": "set", "path": "/last_action", "value": "delete"},
        ],
    )

    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp)
        plan_file = logs_dir / "destroy.plan"
        execution_log = logs_dir / "execution.log"

        try:
            _run_init(module_dir, log, execution_log)

            workspace_mgr = WorkspaceManager(module_dir, log)
            workspace_mgr.select_or_create(workspace_name)

            # Destroy plan first (for audit trail and approval gates)
            planner = TerraformPlan(module_dir, log)
            tf_vars = _build_tf_vars(request)
            plan_result = planner.run(
                var_file=env_var_file,
                extra_vars=tf_vars,
                plan_file=plan_file,
                destroy=True,
            )
            _append_log(execution_log, plan_result.raw_output)

            if not plan_result.success:
                raise RuntimeError(f"terraform destroy plan failed: {plan_result.error_output}")

            # Execute destroy
            destroyer = TerraformDestroy(module_dir, log)
            destroy_result = destroyer.run(
                var_file=env_var_file,
                extra_vars=tf_vars,
            )
            _append_log(execution_log, destroy_result.raw_output)

            if not destroy_result.success:
                raise RuntimeError(f"terraform destroy failed: {destroy_result.error_output}")

            # Soft-delete the inventory record
            inventory.soft_delete(request.request_id, request.project)

            uploader.upload_run_artifacts(request.request_id, logs_dir)
            log.info("DELETE handler completed successfully")

        except DependencyBlockedError:
            raise

        except Exception as exc:
            _append_log(execution_log, f"\n\nFAILED: {exc}\n")
            inventory.mark_failed(request.request_id, request.project, error_message=str(exc))
            try:
                uploader.upload_run_artifacts(request.request_id, logs_dir)
            except Exception:
                pass
            raise
