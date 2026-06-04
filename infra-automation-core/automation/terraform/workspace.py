"""
Terraform workspace manager.

Handles workspace selection and creation before each Terraform run.
The workspace name is deterministic: {org}-{project}-{env}
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from automation.utils.logger import BoundLogger


class WorkspaceManager:
    """
    Ensures the correct Terraform workspace exists and is selected
    before any plan/apply/destroy command runs.
    """

    def __init__(self, working_dir: Path, logger: BoundLogger) -> None:
        self.working_dir = working_dir
        self.log = logger

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=self.working_dir,
            capture_output=True,
            text=True,
            check=False,
        )

    def list_workspaces(self) -> list[str]:
        result = self._run(["terraform", "workspace", "list"])
        workspaces = []
        for line in result.stdout.splitlines():
            name = line.strip().lstrip("* ").strip()
            if name:
                workspaces.append(name)
        return workspaces

    def select_or_create(self, workspace_name: str) -> None:
        """
        Select the workspace if it exists, otherwise create it.
        After this call the workspace is active.
        """
        existing = self.list_workspaces()
        if workspace_name in existing:
            self.log.info("Selecting existing workspace", workspace=workspace_name)
            result = self._run(["terraform", "workspace", "select", workspace_name])
        else:
            self.log.info("Creating new workspace", workspace=workspace_name)
            result = self._run(["terraform", "workspace", "new", workspace_name])

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to select/create workspace '{workspace_name}': {result.stderr}"
            )
        self.log.info("Workspace active", workspace=workspace_name)

    def current_workspace(self) -> str:
        result = self._run(["terraform", "workspace", "show"])
        return result.stdout.strip()
