"""Tests for the structured JSON logger."""

import json
import logging
from io import StringIO

import pytest

from automation.utils.logger import BoundLogger, StructuredJsonFormatter, get_logger


class TestStructuredJsonFormatter:
    def _make_record(self, msg: str, level: int = logging.INFO, **extra) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test.component",
            level=level,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return record

    def test_output_is_valid_json(self):
        formatter = StructuredJsonFormatter()
        record = self._make_record("Hello world")
        output = formatter.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_required_fields_present(self):
        formatter = StructuredJsonFormatter()
        record = self._make_record("test message")
        parsed = json.loads(formatter.format(record))
        assert "timestamp" in parsed
        assert "log_level" in parsed
        assert "component" in parsed
        assert "message" in parsed
        assert parsed["message"] == "test message"

    def test_extra_fields_included(self):
        formatter = StructuredJsonFormatter()
        record = self._make_record("test", request_id="abc-123", workspace="my-ws")
        parsed = json.loads(formatter.format(record))
        assert parsed["request_id"] == "abc-123"
        assert parsed["workspace"] == "my-ws"

    def test_log_level_correct(self):
        formatter = StructuredJsonFormatter()
        record = self._make_record("warning msg", level=logging.WARNING)
        parsed = json.loads(formatter.format(record))
        assert parsed["log_level"] == "WARNING"


class TestBoundLogger:
    def _capture_logs(self) -> tuple[BoundLogger, StringIO]:
        buf = StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(StructuredJsonFormatter())
        base = logging.getLogger(f"test_{id(buf)}")
        base.handlers = [handler]
        base.setLevel(logging.DEBUG)
        base.propagate = False
        bound = BoundLogger(
            base,
            request_id="req-001",
            action="create",
            project="myproject",
        )
        return bound, buf

    def _parse_last_log(self, buf: StringIO) -> dict:
        buf.seek(0)
        lines = [l.strip() for l in buf.getvalue().splitlines() if l.strip()]
        assert lines, "No log output captured"
        return json.loads(lines[-1])

    def test_context_injected(self):
        log, buf = self._capture_logs()
        log.info("test message")
        parsed = self._parse_last_log(buf)
        assert parsed["request_id"] == "req-001"
        assert parsed["action"] == "create"
        assert parsed["project"] == "myproject"
        assert parsed["message"] == "test message"

    def test_extra_kwargs_merged(self):
        log, buf = self._capture_logs()
        log.info("apply done", workspace="acme-webapp-dev", exit_code=0)
        parsed = self._parse_last_log(buf)
        assert parsed["workspace"] == "acme-webapp-dev"
        assert parsed["exit_code"] == 0

    def test_bind_creates_new_logger_with_extra_context(self):
        log, buf = self._capture_logs()
        bound = log.bind(workspace="new-workspace", module="vm")
        bound.info("bound log")
        parsed = self._parse_last_log(buf)
        assert parsed["workspace"] == "new-workspace"
        # 'module' is a reserved stdlib LogRecord field — the logger prefixes it as 'tf_module'
        assert parsed["tf_module"] == "vm"
        assert parsed["request_id"] == "req-001"  # Original context preserved

    def test_bind_does_not_mutate_original(self):
        log, buf = self._capture_logs()
        log.bind(extra_field="value")
        log.info("original log")
        parsed = self._parse_last_log(buf)
        assert "extra_field" not in parsed

    def test_all_levels(self):
        log, buf = self._capture_logs()
        for method in (log.debug, log.info, log.warning, log.error, log.critical):
            method("level test")
        buf.seek(0)
        levels = [json.loads(l)["log_level"] for l in buf.getvalue().splitlines() if l.strip()]
        assert levels == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
