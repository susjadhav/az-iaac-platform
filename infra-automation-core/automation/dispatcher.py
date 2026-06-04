"""
automation/dispatcher.py

Main entry point for the Python automation layer.

The pipeline (GitHub Actions) calls this module with a JSON payload
representing a ProvisioningRequest. The dispatcher:

  1. Parses and validates the request (Pydantic)
  2. Sets up the bound logger with full request context
  3. Sends the pre-execution acknowledgement (if not already sent by the API)
  4. Routes to the correct lifecycle handler
  5. Sends the completion notification email
  6. Exits 0 on success, 1 on failure (pipeline sees the exit code)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from automation.handlers.create import run_create
from automation.handlers.delete import DependencyBlockedError, run_delete
from automation.handlers.modify import run_modify
from automation.handlers.delete import run_refresh  # refresh lives in delete.py
from automation.inventory.cosmos_client import get_inventory_client
from automation.models.request import LifecycleAction, ProvisioningRequest
from automation.utils.blob_uploader import get_uploader
from automation.utils.email_client import (
    build_completion_email,
    get_email_client,
)
from automation.utils.logger import get_logger

# Re-export run_refresh from the correct module for clarity
from automation.handlers.delete import run_refresh


def dispatch(payload: dict) -> None:
    """
    Parse the request payload and route to the correct handler.
    This is the single entry point called by the pipeline.
    """
    # --- Parse & validate request ---
    try:
        request = ProvisioningRequest(**payload)
    except Exception as exc:
        # Can't log with request context yet — use a generic logger
        log = get_logger("dispatcher")
        log.error("Request validation failed", error_message=str(exc))
        sys.exit(1)

    # --- Set up bound logger with full request context ---
    log = get_logger(
        "dispatcher",
        request_id=request.request_id,
        action=request.action.value,
        project=request.project,
        environment=request.environment.value,
        user_email=request.owner_email,
    )

    log.info(
        "Dispatcher received request",
        service_type=request.service_type.value,
        organization=request.organization,
        region=request.region.value,
    )

    # --- Validate service-specific config early ---
    try:
        request.validated_service_config()
    except Exception as exc:
        log.error("Service configuration validation failed", error_message=str(exc))
        _send_failure_email(request, str(exc))
        sys.exit(1)

    # --- Initialise shared dependencies ---
    try:
        inventory = get_inventory_client()
        uploader = get_uploader()
    except Exception as exc:
        log.error("Failed to initialise dependencies", error_message=str(exc))
        sys.exit(1)

    # --- Route to handler ---
    handler_map = {
        LifecycleAction.CREATE: run_create,
        LifecycleAction.MODIFY: run_modify,
        LifecycleAction.REFRESH: run_refresh,
        LifecycleAction.DELETE: run_delete,
    }

    handler = handler_map[request.action]
    handler_log = log.bind(handler=request.action.value)

    try:
        handler(request, inventory, uploader, handler_log)
        _send_success_email(request)
        log.info("Dispatch completed successfully")

    except DependencyBlockedError as exc:
        log.error("Delete blocked by active dependents", error_message=str(exc))
        _send_failure_email(request, str(exc))
        sys.exit(1)

    except Exception as exc:
        log.error("Handler raised unhandled exception", error_message=str(exc))
        _send_failure_email(request, str(exc))
        sys.exit(1)


def _send_success_email(request: ProvisioningRequest) -> None:
    try:
        client = get_email_client()
        msg = build_completion_email(
            to=request.owner_email,
            request_id=request.request_id,
            action=request.action.value,
            service_type=request.service_type.value,
            project=request.project,
            environment=request.environment.value,
            status="success",
            resource_name=request.resource_name(),
            pipeline_run_url=request.pipeline_run_url,
        )
        client.send(msg)
    except Exception:
        pass  # Email failure must never crash the pipeline


def _send_failure_email(request: ProvisioningRequest, error_message: str) -> None:
    try:
        client = get_email_client()
        msg = build_completion_email(
            to=request.owner_email,
            request_id=request.request_id,
            action=request.action.value,
            service_type=request.service_type.value,
            project=request.project,
            environment=request.environment.value,
            status="failed",
            error_message=error_message,
            pipeline_run_url=request.pipeline_run_url,
        )
        client.send(msg)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI entry point — called by GitHub Actions
# ---------------------------------------------------------------------------


def main() -> None:
    """
    CLI entry point.

    Reads the provisioning request JSON from:
      1. PROVISION_REQUEST_JSON environment variable (preferred in CI)
      2. First positional argument as a file path

    Example (CI):
      PROVISION_REQUEST_JSON='{"request_id": "...", ...}' python -m automation.dispatcher

    Example (local):
      python -m automation.dispatcher ./request.json
    """
    raw_json = os.environ.get("PROVISION_REQUEST_JSON")

    if not raw_json:
        if len(sys.argv) < 2:
            print(
                "Usage: python -m automation.dispatcher <request.json>\n"
                "   or: PROVISION_REQUEST_JSON='{...}' python -m automation.dispatcher",
                file=sys.stderr,
            )
            sys.exit(1)
        request_file = Path(sys.argv[1])
        if not request_file.exists():
            print(f"Error: file not found: {request_file}", file=sys.stderr)
            sys.exit(1)
        raw_json = request_file.read_text()

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    dispatch(payload)


if __name__ == "__main__":
    main()
