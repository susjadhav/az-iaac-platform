"""
Terraform plan wrapper.

Runs `terraform plan` with JSON output and captures the structured result
for logging, approval gates, and change summaries.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from automation.utils.logger import BoundLogger


@dataclass
class PlanResult:
    success: bool
    exit_code: int
    has_changes: bool
    plan_summary: str
    plan_json: dict | None = None
    raw_output: str = ""
    error_output: str = ""
    resource_changes: list[dict] = field(default_factory=list)

    @property
    def changes_add(self) -> int:
        return sum(1 for r in self.resource_changes if "create" in r.get("change", {}).get("actions", []))

    @property
    def changes_change(self) -> int:
        return sum(1 for r in self.resource_changes if "update" in r.get("change", {}).get("actions", []))

    @property
    def changes_destroy(self) -> int:
        return sum(1 for r in self.resource_changes if "delete" in r.get("change", {}).get("actions", []))


class TerraformPlan:
    """
    Wraps `terraform plan` with structured JSON output.

    Exit codes:
      0 = success, no changes
      1 = error
      2 = success, changes present (detailed exit mode)
    """

    def __init__(self, working_dir: Path, logger: BoundLogger) -> None:
        self.working_dir = working_dir
        self.log = logger

    def run(
        self,
        var_file: Path | None = None,
        extra_vars: dict[str, str] | None = None,
        plan_file: Path | None = None,
        destroy: bool = False,
        json_output_file: Path | None = None,
    ) -> PlanResult:
        """
        Execute terraform plan.

        Args:
            var_file: Path to a .tfvars file.
            extra_vars: Additional -var key=value pairs.
            plan_file: Write the binary plan to this path (for apply -input=false).
            destroy: Run as a destroy plan.
            json_output_file: Write plan JSON output to this file.
        """
        cmd = ["terraform", "plan", "-detailed-exitcode", "-no-color"]

        if destroy:
            cmd.append("-destroy")

        if var_file and var_file.exists():
            cmd += [f"-var-file={var_file}"]

        for k, v in (extra_vars or {}).items():
            cmd += [f"-var={k}={v}"]

        if plan_file:
            cmd += [f"-out={plan_file}"]

        self.log.info("Running terraform plan", command=" ".join(cmd))

        result = subprocess.run(
            cmd,
            cwd=self.working_dir,
            capture_output=True,
            text=True,
            check=False,
        )

        success = result.returncode in (0, 2)
        has_changes = result.returncode == 2

        plan_json: dict | None = None
        resource_changes: list[dict] = []

        # Parse the JSON output for structured reporting
        if json_output_file and json_output_file.exists():
            try:
                with open(json_output_file) as f:
                    plan_json = json.load(f)
                resource_changes = plan_json.get("resource_changes", [])
            except (json.JSONDecodeError, OSError) as exc:
                self.log.warning("Could not parse plan JSON", error_message=str(exc))

        # Build a human-readable summary
        if has_changes:
            adds = sum(1 for r in resource_changes if "create" in r.get("change", {}).get("actions", []))
            changes = sum(1 for r in resource_changes if "update" in r.get("change", {}).get("actions", []))
            destroys = sum(1 for r in resource_changes if "delete" in r.get("change", {}).get("actions", []))
            summary = f"Plan: {adds} to add, {changes} to change, {destroys} to destroy."
        elif success:
            summary = "No changes. Infrastructure is up-to-date."
        else:
            summary = f"Plan failed (exit code {result.returncode})."

        plan_result = PlanResult(
            success=success,
            exit_code=result.returncode,
            has_changes=has_changes,
            plan_summary=summary,
            plan_json=plan_json,
            raw_output=result.stdout,
            error_output=result.stderr,
            resource_changes=resource_changes,
        )

        self.log.info(
            "Terraform plan complete",
            exit_code=result.returncode,
            has_changes=has_changes,
            plan_summary=summary,
        )

        if not success:
            self.log.error(
                "Terraform plan failed",
                exit_code=result.returncode,
                error_output=result.stderr[:2000],
            )

        return plan_result
