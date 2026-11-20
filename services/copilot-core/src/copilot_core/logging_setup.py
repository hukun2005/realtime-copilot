"""结构化日志：JSON Lines 到 stderr。

两条硬约束：

1. 只输出可序列化的标量字段，超长值截断，避免把整段转写塞进日志。
2. 敏感键名（token / api_key / authorization 等）一律替换为 ``[redacted]``，
   即使调用方误传也不会落盘。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

#: 值必须被替换的键名（小写比较）。
SENSITIVE_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "credential",
    }
)

#: LogRecord 自带字段，不进入结构化输出。
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)

MAX_VALUE_CHARS = 2048


class JsonFormatter(logging.Formatter):
    """把日志记录格式化为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        """渲染一条日志。

        Args:
            record: 标准日志记录。

        Returns:
            单行 JSON 文本。
        """
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage()[:MAX_VALUE_CHARS],
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if key.lower() in SENSITIVE_KEYS:
                payload[key] = "[redacted]"
                continue
            payload[key] = _coerce(key, value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)[:MAX_VALUE_CHARS]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _coerce(key: str, value: Any) -> Any:
    """把任意扩展字段收敛成 JSON 可序列化值。"""
    if isinstance(value, str):
        return value[:MAX_VALUE_CHARS]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return f"<{type(value).__name__}:{key}>"


def configure_logging(level: str = "info") -> None:
    """安装唯一 stderr handler。

    Args:
        level: ``debug`` / ``info`` / ``warning`` / ``error``。
    """
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """返回命名 logger。"""
    return logging.getLogger(name)
