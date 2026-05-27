"""
GitHub Actions pipeline trigger.

Calls the GitHub Actions REST API to dispatch the automation workflow.
Uses workflow_dispatch with the ProvisioningRequest JSON as an input.

Docs: https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
"""

from __future__ import annotations

import json
import os
from typing import Annotated

import httpx
from fastapi import Depends

from automation.models.request import ProvisioningRequest
from automation.utils.logger import get_logger

log = get_logger("api.pipeline_trigger")


class PipelineTrigger:
    """
    Dispatches a GitHub Actions workflow_dispatch event with the
    ProvisioningRequest JSON as the workflow input payload.
    """

    DISPATCH_URL_TEMPLATE = (
        "https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
    )

    def __init__(
        self,
        github_token: str,
        repo_owner: str,
        repo_name: str,
        workflow_id: str = "provision.yml",
        ref: str = "main",
    ) -> None:
        self._token = github_token
        self._owner = repo_owner
        self._repo = repo_name
        self._workflow_id = workflow_id
        self._ref = ref

    async def dispatch(self, request: ProvisioningRequest) -> str | None:
        """
        Dispatch the automation workflow.

        Returns the workflow run URL (best-effort — GitHub does not return
        the run ID synchronously from dispatch, so we return the runs list URL).
        """
        url = self.DISPATCH_URL_TEMPLATE.format(
            owner=self._owner,
            repo=self._repo,
            workflow_id=self._workflow_id,
        )

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # GitHub Actions workflow_dispatch inputs must be strings.
        # We pack the entire request as a JSON string in a single input field.
        payload = {
            "ref": self._ref,
            "inputs": {
                "request_json": request.model_dump_json(),
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)

        if response.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"GitHub Actions dispatch failed: {response.status_code} — {response.text}"
            )

        log.info(
            "Pipeline dispatched",
            request_id=request.request_id,
            workflow=self._workflow_id,
            ref=self._ref,
        )

        # Return the runs list URL as a best-effort link (run ID is async)
        runs_url = (
            f"https://github.com/{self._owner}/{self._repo}/actions"
            f"/workflows/{self._workflow_id}"
        )
        return runs_url


class AzureDevOpsTrigger:
    """
    Alternative pipeline trigger for Azure DevOps.
    Drop-in replacement for PipelineTrigger when using Azure DevOps pipelines.
    """

    def __init__(
        self,
        org_url: str,        # e.g. https://dev.azure.com/myorg
        project: str,
        pipeline_id: int,
        pat_token: str,
    ) -> None:
        self._org_url = org_url.rstrip("/")
        self._project = project
        self._pipeline_id = pipeline_id
        self._pat_token = pat_token

    async def dispatch(self, request: ProvisioningRequest) -> str | None:
        url = (
            f"{self._org_url}/{self._project}/_apis/pipelines/"
            f"{self._pipeline_id}/runs?api-version=7.1-preview.1"
        )
        import base64
        token_b64 = base64.b64encode(f":{self._pat_token}".encode()).decode()

        payload = {
            "resources": {"repositories": {"self": {"refName": "refs/heads/main"}}},
            "variables": {
                "PROVISION_REQUEST_JSON": {
                    "value": request.model_dump_json(),
                    "isSecret": False,
                }
            },
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Basic {token_b64}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Azure DevOps trigger failed: {response.status_code} — {response.text}"
            )

        run_id = response.json().get("id")
        return (
            f"{self._org_url}/{self._project}/_build/results?buildId={run_id}"
            if run_id else None
        )


def get_pipeline_trigger() -> PipelineTrigger:
    """FastAPI dependency that builds the trigger from environment variables."""
    backend = os.environ.get("PIPELINE_BACKEND", "github").lower()

    if backend == "github":
        return PipelineTrigger(
            github_token=os.environ["GITHUB_TOKEN"],
            repo_owner=os.environ["GITHUB_REPO_OWNER"],
            repo_name=os.environ.get("GITHUB_REPO_NAME", "infra-automation-core"),
            workflow_id=os.environ.get("GITHUB_WORKFLOW_ID", "provision.yml"),
            ref=os.environ.get("GITHUB_WORKFLOW_REF", "main"),
        )
    elif backend == "azuredevops":
        return AzureDevOpsTrigger(
            org_url=os.environ["AZDO_ORG_URL"],
            project=os.environ["AZDO_PROJECT"],
            pipeline_id=int(os.environ["AZDO_PIPELINE_ID"]),
            pat_token=os.environ["AZDO_PAT_TOKEN"],
        )
    else:
        raise ValueError(f"Unknown PIPELINE_BACKEND: {backend!r}")
