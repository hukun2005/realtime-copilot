"""统一的类型化错误。

错误码来自协议 schema 的 ``ErrorCode``，保证线上可路由；``message`` 只用于诊断。
"""

from __future__ import annotations

from copilot_protocol import ErrorCode


class CopilotError(Exception):
    """Core 内部错误；会映射成 ``system.error``。

    Args:
        code: 协议错误码。
        message: 面向诊断的单行说明，不得包含密钥或转写正文。
        retryable: 客户端是否值得重试。
    """

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ConfigError(Exception):
    """配置非法；启动期抛出，进程以非零码退出。"""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = path

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message
