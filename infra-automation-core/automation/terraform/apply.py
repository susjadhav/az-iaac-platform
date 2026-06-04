"""
Terraform apply and destroy wrappers.

apply  — provisions or updates resources (Create / Modify)
destroy — tears down resources (Delete)
refresh — syncs state with actual Azure resources
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from automation.utils.logger import BoundLogger


@dataclass
class ApplyResult:
    success: bool
    exit_code: int
    outputs: dict[str, str] = field(default_factory=dict)
    raw_output: str = ""
    error_output: str = ""

    @property
    def resource_id_output(self) -> str | None:
        """Convenience: returns the 'resource_id' Terraform output if present."""
        val = self.outputs.get("resource_id")
        if isinstance(val, dict):
            return val.get("value")
        return val


@dataclass
class DestroyResult:
    success: bool
    exit_code: int
    raw_output: str = ""
    error_output: str = ""


@dataclass
class RefreshResult:
    success: bool
    exit_code: int
    raw_output: str = ""
    error_output: str = ""


class TerraformApply:
    """Wraps `terraform apply`."""

    def __init__(self, working_dir: Path, logger: BoundLogger) -> None:
        self.working_dir = working_dir
        self.log = logger

    def run(
        self,
        plan_file: Path | None = None,
        var_file: Path | None = None,
        extra_vars: dict[str, str] | None = None,
        auto_approve: bool = True,
    ) -> ApplyResult:
        """
        Execute terraform apply.

        When plan_file is provided (recommended), no -var flags are needed
        since the plan already encoded them. Otherwise, var_file and extra_vars
        are passed directly.
        """
        cmd = ["terraform", "apply", "-no-color"]

        if auto_approve:
            cmd.append("-auto-approve")

        if plan_file:
            cmd.append(str(plan_file))
        else:
            if var_file and var_file.exists():
                cmd += [f"-var-file={var_file}"]
            for k, v in (extra_vars or {}).items():
                cmd += [f"-var={k}={v}"]

        self.log.info("Running terraform apply", command=" ".join(cmd))

        result = subprocess.run(
            cmd,
            cwd=self.working_dir,
            capture_output=True,
            text=True,
            check=False,
        )

        outputs: dict[str, str] = {}
        if result.returncode == 0:
            outputs = self._parse_outputs()

        apply_result = ApplyResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            outputs=outputs,
            raw_output=result.stdout,
            error_output=result.stderr,
        )

        if apply_result.success:
            self.log.info(
                "Terraform apply succeeded",
                exit_code=result.returncode,
                apply_output=result.stdout[-1000:],
            )
        else:
            self.log.error(
                "Terraform apply failed",
                exit_code=result.returncode,
                error_output=result.stderr[:3000],
            )

        return apply_result

    def _parse_outputs(self) -> dict[str, str]:
        """Run `terraform output -json` and return the parsed outputs dict."""
        result = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=self.working_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}


class TerraformDestroy:
    """Wraps `terraform destroy`."""

    def __init__(self, working_dir: Path, logger: BoundLogger) -> None:
        self.working_dir = working_dir
        self.log = logger

    def run(
        self,
        var_file: Path | None = None,
        extra_vars: dict[str, str] | None = None,
        auto_approve: bool = True,
    ) -> DestroyResult:
        cmd = ["terraform", "destroy", "-no-color"]
        if auto_approve:
            cmd.append("-auto-approve")
        if var_file and var_file.exists():
            cmd += [f"-var-file={var_file}"]
        for k, v in (extra_vars or {}).items():
            cmd += [f"-var={k}={v}"]

        self.log.info("Running terraform destroy", command=" ".join(cmd))

        result = subprocess.run(
            cmd,
            cwd=self.working_dir,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            self.log.info("Terraform destroy succeeded")
        else:
            self.log.error(
                "Terraform destroy failed",
                exit_code=result.returncode,
                error_output=result.stderr[:3000],
            )

        return DestroyResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            raw_output=result.stdout,
            error_output=result.stderr,
        )


class TerraformRefresh:
    """Wraps `terraform refresh`."""

    def __init__(self, working_dir: Path, logger: BoundLogger) -> None:
        self.working_dir = working_dir
        self.log = logger

    def run(
        self,
        var_file: Path | None = None,
        extra_vars: dict[str, str] | None = None,
    ) -> RefreshResult:
        cmd = ["terraform", "refresh", "-no-color"]
        if var_file and var_file.exists():
            cmd += [f"-var-file={var_file}"]
        for k, v in (extra_vars or {}).items():
            cmd += [f"-var={k}={v}"]

        self.log.info("Running terraform refresh", command=" ".join(cmd))

        result = subprocess.run(
            cmd,
            cwd=self.working_dir,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            self.log.info("Terraform refresh succeeded")
        else:
            self.log.error(
                "Terraform refresh failed",
                exit_code=result.returncode,
                error_output=result.stderr[:3000],
            )

        return RefreshResult(
            success=result.returncode == 0,
            exit_code=result.returncode,
            raw_output=result.stdout,
            error_output=result.stderr,
        )
