"""Tests for structured logging configuration."""

from __future__ import annotations

import json
import logging

import pytest
from open_workflow_agent.logging_config import (
    JsonFormatter,
    configure_logging,
    setup_structured_logging,
    setup_text_logging,
)


def test_json_formatter_produces_valid_json() -> None:
    """Verify JsonFormatter produces valid JSON output."""
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    output = formatter.format(record)
    data = json.loads(output)

    assert "timestamp" in data
    assert data["level"] == "INFO"
    assert data["logger"] == "test.logger"
    assert data["message"] == "Test message"


def test_json_formatter_includes_exception_info() -> None:
    """Verify JsonFormatter includes exception info when present."""
    formatter = JsonFormatter()

    try:
        raise ValueError("test error")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test.logger",
        level=logging.ERROR,
        pathname="test.py",
        lineno=10,
        msg="Error occurred",
        args=(),
        exc_info=exc_info,
    )

    output = formatter.format(record)
    data = json.loads(output)

    assert "exception" in data
    assert "ValueError: test error" in data["exception"]


def test_json_formatter_handles_extra_fields() -> None:
    """Verify JsonFormatter includes extra fields."""
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    record.extra_fields = {"request_id": "req-123", "user_id": "user-456"}

    output = formatter.format(record)
    data = json.loads(output)

    assert data["request_id"] == "req-123"
    assert data["user_id"] == "user-456"


def test_setup_structured_logging_configures_root_logger() -> None:
    """Verify setup_structured_logging configures the root logger correctly."""
    setup_structured_logging("DEBUG")

    root_logger = logging.getLogger()
    assert root_logger.level == logging.DEBUG
    assert len(root_logger.handlers) == 1
    assert isinstance(root_logger.handlers[0].formatter, JsonFormatter)


def test_setup_text_logging_configures_root_logger() -> None:
    """Verify setup_text_logging configures the root logger correctly."""
    setup_text_logging("WARNING")

    root_logger = logging.getLogger()
    assert root_logger.level == logging.WARNING
    assert len(root_logger.handlers) == 1
    assert not isinstance(root_logger.handlers[0].formatter, JsonFormatter)


def test_configure_logging_selects_structured() -> None:
    """Verify configure_logging selects structured logging when requested."""
    configure_logging("INFO", structured=True)

    root_logger = logging.getLogger()
    assert isinstance(root_logger.handlers[0].formatter, JsonFormatter)


def test_configure_logging_selects_text() -> None:
    """Verify configure_logging selects text logging by default."""
    configure_logging("INFO", structured=False)

    root_logger = logging.getLogger()
    assert not isinstance(root_logger.handlers[0].formatter, JsonFormatter)


def test_structured_logging_output_is_valid_json(capfd: pytest.CaptureFixture[str]) -> None:
    """Verify structured logging produces valid JSON output."""
    setup_structured_logging("INFO")

    logger = logging.getLogger("test.structured")
    logger.info("Test structured message")

    # Capture stdout output
    captured = capfd.readouterr()
    assert captured.out

    # The output should be valid JSON
    data = json.loads(captured.out.strip())
    assert data["message"] == "Test structured message"
    assert data["level"] == "INFO"


def test_logging_suppresses_noisy_libraries() -> None:
    """Verify noisy library loggers are suppressed."""
    setup_structured_logging("DEBUG")

    # These loggers should be set to WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("uvicorn.access").level == logging.WARNING
