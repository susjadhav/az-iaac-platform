"""
Email notification client.

Sends the two-stage notifications described in the architecture doc:
  1. Acknowledgement — immediately after request is received (Step 4)
  2. Completion      — after Terraform finishes (Step 9)

Supports SendGrid (preferred for production) with an SMTP fallback
for local/dev use. Backend is selected via EMAIL_BACKEND env var.
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Literal


@dataclass
class EmailMessage:
    to: str
    subject: str
    body_text: str       # Plain-text fallback
    body_html: str | None = None


class EmailClient:
    """Abstract email client — do not instantiate directly; use factory."""

    def send(self, message: EmailMessage) -> None:
        raise NotImplementedError


class SendGridEmailClient(EmailClient):
    """SendGrid via the official Python SDK."""

    def __init__(self, api_key: str, sender_email: str) -> None:
        # Lazy import — sendgrid is an optional dependency
        try:
            import sendgrid
            from sendgrid.helpers.mail import Mail
            self._sendgrid = sendgrid
            self._Mail = Mail
        except ImportError as e:
            raise RuntimeError(
                "sendgrid package not installed. Run: pip install sendgrid"
            ) from e

        self._client = sendgrid.SendGridAPIClient(api_key=api_key)
        self._sender = sender_email

    def send(self, message: EmailMessage) -> None:
        mail = self._Mail(
            from_email=self._sender,
            to_emails=message.to,
            subject=message.subject,
            plain_text_content=message.body_text,
            html_content=message.body_html,
        )
        response = self._client.send(mail)
        if response.status_code not in (200, 202):
            raise RuntimeError(
                f"SendGrid returned {response.status_code}: {response.body}"
            )


class SmtpEmailClient(EmailClient):
    """SMTP client — for local dev and environments without SendGrid."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender_email: str,
        use_tls: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender_email
        self._use_tls = use_tls

    def send(self, message: EmailMessage) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = message.subject
        msg["From"] = self._sender
        msg["To"] = message.to

        msg.attach(MIMEText(message.body_text, "plain"))
        if message.body_html:
            msg.attach(MIMEText(message.body_html, "html"))

        with smtplib.SMTP(self._host, self._port) as server:
            if self._use_tls:
                server.starttls()
            server.login(self._username, self._password)
            server.sendmail(self._sender, message.to, msg.as_string())


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------


def build_acknowledgement_email(
    to: str,
    request_id: str,
    action: str,
    service_type: str,
    project: str,
    environment: str,
) -> EmailMessage:
    subject = f"[IAaC Platform] Request received — {action.upper()} {service_type} ({request_id[:8]})"
    text = (
        f"Your infrastructure request has been received and is being processed.\n\n"
        f"Request ID : {request_id}\n"
        f"Action     : {action.upper()}\n"
        f"Service    : {service_type}\n"
        f"Project    : {project}\n"
        f"Environment: {environment}\n\n"
        "You will receive a second email when provisioning completes.\n\n"
        "— Infrastructure Automation Platform"
    )
    html = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px">
  <h2 style="color:#0066cc">Request received</h2>
  <p>Your infrastructure request is being processed.</p>
  <table style="border-collapse:collapse;width:100%">
    <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:bold">Request ID</td>
        <td style="padding:6px 12px;font-family:monospace">{request_id}</td></tr>
    <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:bold">Action</td>
        <td style="padding:6px 12px">{action.upper()}</td></tr>
    <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:bold">Service</td>
        <td style="padding:6px 12px">{service_type}</td></tr>
    <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:bold">Project</td>
        <td style="padding:6px 12px">{project}</td></tr>
    <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:bold">Environment</td>
        <td style="padding:6px 12px">{environment}</td></tr>
  </table>
  <p style="color:#666;margin-top:24px">
    You will receive a second email when provisioning completes.
  </p>
  <p style="color:#999;font-size:12px">— Infrastructure Automation Platform</p>
</body></html>
"""
    return EmailMessage(to=to, subject=subject, body_text=text, body_html=html)


def build_completion_email(
    to: str,
    request_id: str,
    action: str,
    service_type: str,
    project: str,
    environment: str,
    status: Literal["success", "failed"],
    resource_name: str | None = None,
    azure_resource_id: str | None = None,
    pipeline_run_url: str | None = None,
    error_message: str | None = None,
) -> EmailMessage:
    icon = "✅" if status == "success" else "❌"
    subject = (
        f"[IAaC Platform] {icon} {action.upper()} {status.upper()} — "
        f"{service_type} ({request_id[:8]})"
    )
    pipeline_line = f"Pipeline run: {pipeline_run_url}" if pipeline_run_url else ""
    resource_line = f"Resource     : {resource_name}" if resource_name else ""
    arm_line = f"ARM ID       : {azure_resource_id}" if azure_resource_id else ""
    error_line = f"\nError detail:\n{error_message}" if error_message else ""

    text = (
        f"Provisioning {status}.\n\n"
        f"Request ID : {request_id}\n"
        f"Action     : {action.upper()}\n"
        f"Service    : {service_type}\n"
        f"Project    : {project}\n"
        f"Environment: {environment}\n"
        f"{resource_line}\n"
        f"{arm_line}\n"
        f"{pipeline_line}"
        f"{error_line}\n\n"
        "— Infrastructure Automation Platform"
    )
    return EmailMessage(to=to, subject=subject, body_text=text)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_email_client() -> EmailClient:
    """
    Reads EMAIL_BACKEND from environment and returns the correct client.
    EMAIL_BACKEND: "sendgrid" (default) | "smtp"
    """
    backend = os.environ.get("EMAIL_BACKEND", "sendgrid").lower()

    if backend == "sendgrid":
        return SendGridEmailClient(
            api_key=os.environ["SENDGRID_API_KEY"],
            sender_email=os.environ.get("EMAIL_SENDER", "noreply@iaac-platform.io"),
        )
    elif backend == "smtp":
        return SmtpEmailClient(
            host=os.environ["SMTP_HOST"],
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=os.environ["SMTP_USERNAME"],
            password=os.environ["SMTP_PASSWORD"],
            sender_email=os.environ.get("EMAIL_SENDER", "noreply@iaac-platform.io"),
        )
    else:
        raise ValueError(f"Unknown EMAIL_BACKEND: {backend!r}. Use 'sendgrid' or 'smtp'.")
