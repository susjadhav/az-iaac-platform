"""
Azure Blob Storage uploader.

Uploads local execution logs and Terraform plan JSON to the centralized
log storage account after each pipeline run (success or failure).

Blob path convention (from architecture doc):
  logs/{request_id}/execution.log   → 90-day retention
  logs/{request_id}/plan.json       → 90-day retention
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

if TYPE_CHECKING:
    pass


class BlobUploader:
    """
    Uploads files to Azure Blob Storage using DefaultAzureCredential
    (managed identity in CI, az login locally).
    """

    def __init__(self, storage_account_name: str, container_name: str = "logs") -> None:
        self.account_url = f"https://{storage_account_name}.blob.core.windows.net"
        self.container_name = container_name
        self._client: BlobServiceClient | None = None

    def _get_client(self) -> BlobServiceClient:
        if self._client is None:
            credential = DefaultAzureCredential()
            self._client = BlobServiceClient(self.account_url, credential=credential)
        return self._client

    def upload_file(
        self,
        local_path: Path,
        blob_name: str,
        content_type: str = "application/octet-stream",
        overwrite: bool = True,
    ) -> str:
        """
        Upload a local file to blob storage.

        Returns the full blob URL.
        """
        client = self._get_client()
        container = client.get_container_client(self.container_name)
        blob = container.get_blob_client(blob_name)

        with open(local_path, "rb") as f:
            blob.upload_blob(
                f,
                overwrite=overwrite,
                content_settings=ContentSettings(content_type=content_type),
            )

        return f"{self.account_url}/{self.container_name}/{blob_name}"

    def upload_text(
        self,
        content: str,
        blob_name: str,
        content_type: str = "text/plain; charset=utf-8",
        overwrite: bool = True,
    ) -> str:
        """Upload an in-memory string directly (avoids temp file)."""
        client = self._get_client()
        container = client.get_container_client(self.container_name)
        blob = container.get_blob_client(blob_name)

        blob.upload_blob(
            content.encode("utf-8"),
            overwrite=overwrite,
            content_settings=ContentSettings(content_type=content_type),
        )
        return f"{self.account_url}/{self.container_name}/{blob_name}"

    def upload_run_artifacts(self, request_id: str, logs_dir: Path) -> dict[str, str]:
        """
        Upload all artifacts for a pipeline run.

        Looks for:
          {logs_dir}/execution.log
          {logs_dir}/plan.json

        Returns a dict of artifact_name → blob_url.
        """
        urls: dict[str, str] = {}
        artifacts = {
            "execution.log": "text/plain; charset=utf-8",
            "plan.json": "application/json",
        }
        for filename, content_type in artifacts.items():
            local = logs_dir / filename
            if local.exists():
                blob_name = f"logs/{request_id}/{filename}"
                url = self.upload_file(local, blob_name, content_type=content_type)
                urls[filename] = url

        return urls


def get_uploader() -> BlobUploader:
    """Factory: reads storage account name from environment."""
    account = os.environ["AZURE_LOG_STORAGE_ACCOUNT"]
    container = os.environ.get("AZURE_LOG_CONTAINER", "logs")
    return BlobUploader(account, container)
