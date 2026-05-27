"""
Structured JSON logger for the automation platform.

Every component imports get_logger() and emits structured records.
All log entries include the standard fields defined in the architecture doc:
  timestamp, log_level, component, request_id, action, project,
  environment, user_email, message

Terraform-specific and error fields are added by callers via extra kwargs.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    # Standard fields always present — extra fields from LogRecord.__dict__
    STANDARD_FIELDS = {
        "timestamp", "log_level", "component", "request_id",
        "action", "project", "environment", "user_email", "message",
    }

    TERRAFORM_FIELDS = {
        "workspace", "module", "resource_address",
        "plan_summary", "apply_output", "exit_code",
    }

    ERROR_FIELDS = {"error_type", "error_message", "stack_trace", "retry_attempt"}

    # Fields from LogRecord we don't want to surface
    _SKIP = {
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "msg",
        "name", "pathname", "process", "processName", "relativeCreated",
        "stack_info", "thread", "threadName",
    }

    # stdlib LogRecord field names that callers must NOT use as extra keys
    # (passing them would raise KeyError in makeRecord)
    _RESERVED = {
        "module", "name", "filename", "funcName", "lineno", "pathname",
        "process", "processName", "thread", "threadName", "levelno", "levelname",
        "created", "msecs", "relativeCreated", "args", "msg",
    }

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "log_level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
        }

        # Merge any extra fields the caller attached
        for key, value in record.__dict__.items():
            if key not in self._SKIP and not key.startswith("_"):
                log_entry[key] = value

        if record.exc_info:
            log_entry["stack_trace"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def get_logger(
    component: str,
    *,
    request_id: str | None = None,
    action: str | None = None,
    project: str | None = None,
    environment: str | None = None,
    user_email: str | None = None,
    level: int = logging.INFO,
) -> "BoundLogger":
    """
    Returns a BoundLogger that prepends context fields to every record.

    Usage:
        log = get_logger("handlers.create", request_id=req.request_id,
                         action="create", project="acme-webapp")
        log.info("Terraform apply succeeded", workspace="acme-webapp-prod")
    """
    base = logging.getLogger(component)
    if not base.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter())
        base.addHandler(handler)
        base.setLevel(level)
        base.propagate = False

    return BoundLogger(
        base,
        request_id=request_id,
        action=action,
        project=project,
        environment=environment,
        user_email=user_email,
    )


class BoundLogger:
    """
    Wraps a stdlib Logger and injects context fields into every record
    without requiring callers to repeat them on every call.
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        request_id: str | None = None,
        action: str | None = None,
        project: str | None = None,
        environment: str | None = None,
        user_email: str | None = None,
    ) -> None:
        self._logger = logger
        self._context: dict[str, Any] = {}
        if request_id:
            self._context["request_id"] = request_id
        if action:
            self._context["action"] = action
        if project:
            self._context["project"] = project
        if environment:
            self._context["environment"] = environment
        if user_email:
            self._context["user_email"] = user_email

    def _emit(self, level: int, msg: str, **extra: Any) -> None:
        merged = {**self._context, **extra}
        _RESERVED = {
            "module", "name", "filename", "funcName", "lineno", "pathname",
            "process", "processName", "thread", "threadName", "levelno",
            "levelname", "created", "msecs", "relativeCreated", "args", "msg",
        }
        safe: dict[str, Any] = {}
        for k, v in merged.items():
            safe[f"tf_{k}" if k in _RESERVED else k] = v
        self._logger.log(level, msg, extra=safe)

    def debug(self, msg: str, **extra: Any) -> None:
        self._emit(logging.DEBUG, msg, **extra)

    def info(self, msg: str, **extra: Any) -> None:
        self._emit(logging.INFO, msg, **extra)

    def warning(self, msg: str, **extra: Any) -> None:
        self._emit(logging.WARNING, msg, **extra)

    def error(self, msg: str, **extra: Any) -> None:
        self._emit(logging.ERROR, msg, **extra)

    def critical(self, msg: str, **extra: Any) -> None:
        self._emit(logging.CRITICAL, msg, **extra)

    def bind(self, **extra: Any) -> "BoundLogger":
        """Return a new BoundLogger with additional context fields merged in."""
        new = BoundLogger(self._logger)
        new._context = {**self._context, **extra}
        return new
