"""结构化日志：JSON 形状、脱敏与截断。"""

from __future__ import annotations

import io
import json
import logging
import sys

from copilot_core.logging_setup import JsonFormatter, configure_logging


def capture(*, extra: dict | None = None, exc: bool = False, message: str = "hello") -> list[dict]:  # type: ignore[type-arg]
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())

    logger = logging.getLogger("test.json")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    if exc:
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception(message, extra=extra or {})
    else:
        logger.info(message, extra=extra or {})

    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_basic_fields() -> None:
    records = capture()
    assert len(records) == 1
    record = records[0]
    assert record["level"] == "info"
    assert record["logger"] == "test.json"
    assert record["msg"] == "hello"
    assert record["ts"].endswith("+00:00")


def test_extra_fields_are_included() -> None:
    record = capture(extra={"role": "ui", "connections": 2})[0]
    assert record["role"] == "ui"
    assert record["connections"] == 2


def test_sensitive_keys_are_redacted() -> None:
    record = capture(extra={"token": "supersecret", "api_key": "sk-123", "authorization": "Bearer x"})[0]
    assert record["token"] == "[redacted]"
    assert record["api_key"] == "[redacted]"
    assert record["authorization"] == "[redacted]"


def test_long_values_are_truncated() -> None:
    record = capture(extra={"note": "x" * 5000})[0]
    assert len(record["note"]) == 2048


def test_non_serializable_values_are_replaced() -> None:
    record = capture(extra={"obj": object()})[0]
    assert record["obj"].startswith("<object:")


def test_exception_is_rendered() -> None:
    record = capture(exc=True)[0]
    assert "ValueError: boom" in record["exc"]


def test_configure_logging_installs_single_handler() -> None:
    configure_logging("debug")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.DEBUG
    assert root.handlers[0].stream is sys.stderr
