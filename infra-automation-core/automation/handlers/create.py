"""
Create handler — provisions new Azure resources.

Implements the full create lifecycle:
  1. Write inventory record (status: provisioning)
  2. terraform init + workspace select/create
  3. terraform plan (detect drift / validate)
  4. terraform apply
  5. Extract outputs → update inventory (status: active)
  6. Upload logs to blob storage
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from automation.inventory.cosmos_client import InventoryClient
from automation.models.inventory import InventoryRecord, LastAction
from automation.models.request import ProvisioningRequest
from automation.terraform.apply import TerraformApply
from automation.terraform.plan import TerraformPlan
from automation.terraform.workspace import WorkspaceManager
from automation.utils.blob_uploader import BlobUploader
from automation.utils.logger import BoundLogger


def _terraform_modules_root() -> Path:
    """Resolve the Terraform modules root from an env var or a sensible default."""
    return Path(os.environ.get("TF_MODULES_ROOT", "/opt/infra-terraform-modules"))


def _build_tf_vars(request: ProvisioningRequest) -> dict[str, str]:
    """
    Translate a ProvisioningRequest into Terraform -var key=value pairs.
    Service-specific vars are added by the service config expansion below.
    """
    cfg = request.validated_service_config()
    config_dict = cfg.model_dump()

    tf_vars: dict[str, str] = {
        "organization": request.organization,
        "project": request.project,
        "environment": request.environment.value,
        "region": request.region.value,
        "resource_name": request.resource_name(),
        "owner_email": request.owner_email,
        "request_id": request.request_id,
    }
    # Flatten service config into vars (all values coerced to str for -var flags)
    for k, v in config_dict.items():
        if v is not None:
            tf_vars[k] = str(v).lower() if isinstance(v, bool) else str(v)

    return tf_vars


def run_create(
    request: ProvisioningRequest,
    inventory: InventoryClient,
    uploader: BlobUploader,
    log: BoundLogger,
) -> None:
    """Execute the CREATE lifecycle action end-to-end."""

    workspace_name = request.terraform_workspace_name()
    resource_name = request.resource_name()
    modules_root = _terraform_modules_root()
    module_dir = modules_root / "modules" / "services" / request.service_type.value
    env_var_file = modules_root / "environments" / f"{request.environment.value}.tfvars"

    log.info(
        "Starting CREATE handler",
        workspace=workspace_name,
        module_dir=str(module_dir),
        resource_name=resource_name,
    )

    # 1. Pre-provisioning inventory write
    record = InventoryRecord(
        id=request.request_id,
        request_id=request.request_id,
        resource_name=resource_name,
        resource_type=request.service_type.value,
        organization=request.organization,
        project=request.project,
        environment=request.environment.value,
        region=request.region.value,
        owner_email=request.owner_email,
        requestor_name=request.requestor_name,
        terraform_workspace=workspace_name,
        state_blob_key=request.state_blob_key(),
        pipeline_run_url=request.pipeline_run_url,
        last_action=LastAction.CREATE,
    )
    record.tags = record.standard_tags()
    inventory.create_record(record)

    with tempfile.TemporaryDirectory() as tmp:
        logs_dir = Path(tmp)
        plan_file = logs_dir / "tfplan.binary"
        execution_log = logs_dir / "execution.log"
        plan_json_file = module_dir / ".plan.json"

        try:
            # 2. terraform init
            _run_init(module_dir, log, execution_log)

            # 3. Workspace
            workspace_mgr = WorkspaceManager(module_dir, log)
            workspace_mgr.select_or_create(workspace_name)

            # 4. Plan
            planner = TerraformPlan(module_dir, log)
            tf_vars = _build_tf_vars(request)
            plan_result = planner.run(
                var_file=env_var_file,
                extra_vars=tf_vars,
                plan_file=plan_file,
                json_output_file=plan_json_file,
            )
            _append_log(execution_log, plan_result.raw_output)

            if not plan_result.success:
                raise RuntimeError(f"terraform plan failed: {plan_result.error_output}")

            log.info("Plan summary", plan_summary=plan_result.plan_summary)

            # 5. Apply
            applier = TerraformApply(module_dir, log)
            apply_result = applier.run(plan_file=plan_file)
            _append_log(execution_log, apply_result.raw_output)

            if not apply_result.success:
                raise RuntimeError(f"terraform apply failed: {apply_result.error_output}")

            # 6. Update inventory to active
            azure_resource_id = apply_result.resource_id_output or ""
            inventory.mark_active(
                request_id=request.request_id,
                project=request.project,
                azure_resource_id=azure_resource_id,
                resource_name=resource_name,
                configuration_snapshot=request.configuration,
            )

            # 7. Upload logs
            if plan_json_file.exists():
                plan_json_file.rename(logs_dir / "plan.json")
            uploader.upload_run_artifacts(request.request_id, logs_dir)

            log.info("CREATE handler completed successfully", resource_name=resource_name)

        except Exception as exc:
            _append_log(execution_log, f"\n\nFAILED: {exc}\n")
            inventory.mark_failed(
                request.request_id, request.project, error_message=str(exc)
            )
            try:
                uploader.upload_run_artifacts(request.request_id, logs_dir)
            except Exception:
                pass
            raise


def _run_init(module_dir: Path, log: BoundLogger, execution_log: Path) -> None:
    import subprocess

    result = subprocess.run(
        ["terraform", "init", "-no-color", "-input=false"],
        cwd=module_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    _append_log(execution_log, result.stdout)
    if result.returncode != 0:
        raise RuntimeError(f"terraform init failed: {result.stderr}")
    log.info("Terraform init succeeded")


def _append_log(path: Path, content: str) -> None:
    with open(path, "a") as f:
        f.write(content)
        f.write("\n")
