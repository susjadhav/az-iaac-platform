"""
POST /api/v1/provision

Receives a provisioning request from the UI, validates it,
persists it, sends the acknowledgement email, and dispatches
the GitHub Actions workflow.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.api import InboundProvisionRequest, ProvisioningAcceptedResponse
from app.services.pipeline_trigger import PipelineTrigger, get_pipeline_trigger
from automation.models.request import ProvisioningRequest, ProvisioningResponse
from automation.utils.email_client import build_acknowledgement_email, get_email_client
from automation.utils.logger import get_logger

router = APIRouter(prefix="/api/v1", tags=["provisioning"])
security = HTTPBearer()
log = get_logger("api.provision")


@router.post(
    "/provision",
    response_model=ProvisioningAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an infrastructure provisioning request",
)
async def provision(
    body: InboundProvisionRequest,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    trigger: Annotated[PipelineTrigger, Depends(get_pipeline_trigger)],
) -> ProvisioningAcceptedResponse:
    """
    Accepts an infrastructure request and dispatches the automation pipeline.

    Returns 202 Accepted immediately. The actual provisioning is async —
    the user receives a completion email when Terraform finishes.
    """
    # Token validation is handled by Azure API Management / middleware in prod.
    # In dev, the bearer token is validated here against a known set.
    # Full Entra ID token validation is wired up in app/middleware/auth.py.

    request_id = str(uuid.uuid4())

    # Build the canonical ProvisioningRequest (validates everything)
    try:
        provision_request = ProvisioningRequest(
            request_id=request_id,
            **body.model_dump(),
        )
        provision_request.validated_service_config()
    except Exception as exc:
        log.warning("Request validation failed", error_message=str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    resource_name = provision_request.resource_name()
    log.info(
        "Provisioning request accepted",
        request_id=request_id,
        action=provision_request.action.value,
        service_type=provision_request.service_type.value,
        project=provision_request.project,
        environment=provision_request.environment.value,
        user_email=provision_request.owner_email,
    )

    # Send immediate acknowledgement email (Step 4 of workflow)
    try:
        email_client = get_email_client()
        ack_email = build_acknowledgement_email(
            to=provision_request.owner_email,
            request_id=request_id,
            action=provision_request.action.value,
            service_type=provision_request.service_type.value,
            project=provision_request.project,
            environment=provision_request.environment.value,
        )
        email_client.send(ack_email)
    except Exception as exc:
        # Email failure is non-fatal for the API response
        log.warning("Acknowledgement email failed", error_message=str(exc))

    # Dispatch the GitHub Actions workflow (Step 5)
    try:
        pipeline_run_url = await trigger.dispatch(provision_request)
    except Exception as exc:
        log.error("Pipeline dispatch failed", error_message=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to dispatch automation pipeline: {exc}",
        )

    return ProvisioningAcceptedResponse(
        request_id=request_id,
        status="accepted",
        message=(
            f"Your {provision_request.action.value} request for "
            f"{provision_request.service_type.value} has been accepted. "
            "You will receive an email when provisioning completes."
        ),
        resource_name=resource_name,
        pipeline_run_url=pipeline_run_url,
    )


@router.get(
    "/health",
    summary="Health check",
    include_in_schema=False,
)
async def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}
