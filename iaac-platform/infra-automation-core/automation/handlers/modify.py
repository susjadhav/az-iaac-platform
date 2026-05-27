"""
Modify handler — updates an existing Azure resource's configuration.

Mirrors the CREATE flow but:
  - Marks record as 'modifying' first
  - Re-applies to an existing workspace (resource already exists in state)
  - Updates configuration snapshot and last_modified_at on success
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from automation.handlers.create import _append_log, _build_tf_vars, _run_init
from automation.inventory.cosmos_client import InventoryClient
from automation.models.request import ProvisioningRequest
from automation.terraform.apply import TerraformApply
from automation.terraform.plan import TerraformPlan
from automation.terraform.workspace import WorkspaceManager
from automation.utils.blob_uploader import BlobUploader
from automation.utils.logger import BoundLogger


def run_modify(
    request: ProvisioningRequest,
    inventory: InventoryClient,
    uploader: BlobUploader,
    log: BoundLogger,
) -> None:
    """Execute the MODIFY lifecycle action."""

    workspace_name = request.terraform_workspace_name()
    modules_root = Path(os.environ.get("TF_MODULES_ROOT", "/opt/infra-terraform-modules"))
    module_dir = modules_root / "modules" / "services" / request.service_type.value
    env_var_file = modules_root / "environments" / f"{request.environment.value}.tfvars"

    log.info("Starting MODIFY handler", workspace=workspace_name)
    inventory.mark_modifying(request.request_id, request.project)

    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp)
        plan_file = logs_dir / "tfplan.binary"
        execution_log = logs_dir / "execution.log"

        try:
            _run_init(module_dir, log, execution_log)

            workspace_mgr = WorkspaceManager(module_dir, log)
            workspace_mgr.select_or_create(workspace_name)

            planner = TerraformPlan(module_dir, log)
            tf_vars = _build_tf_vars(request)
            plan_result = planner.run(
                var_file=env_var_file,
                extra_vars=tf_vars,
                plan_file=plan_file,
            )
            _append_log(execution_log, plan_result.raw_output)

            if not plan_result.success:
                raise RuntimeError(f"terraform plan failed: {plan_result.error_output}")

            if not plan_result.has_changes:
                log.info("No changes detected — nothing to modify")
                inventory.mark_modified(
                    request.request_id, request.project,
                    configuration_snapshot=request.configuration,
                )
                return

            applier = TerraformApply(module_dir, log)
            apply_result = applier.run(plan_file=plan_file)
            _append_log(execution_log, apply_result.raw_output)

            if not apply_result.success:
                raise RuntimeError(f"terraform apply failed: {apply_result.error_output}")

            inventory.mark_modified(
                request.request_id,
                request.project,
                configuration_snapshot=request.configuration,
            )
            uploader.upload_run_artifacts(request.request_id, logs_dir)
            log.info("MODIFY handler completed successfully")

        except Exception as exc:
            _append_log(execution_log, f"\n\nFAILED: {exc}\n")
            inventory.mark_failed(request.request_id, request.project, error_message=str(exc))
            try:
                uploader.upload_run_artifacts(request.request_id, logs_dir)
            except Exception:
                pass
            raise
