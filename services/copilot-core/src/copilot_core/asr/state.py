"""ASR 连接状态：与采集状态机完全独立。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum


class AsrState(StrEnum):
    """ASR 连接状态。"""

    IDLE = "idle"
    CONNECTING = "connecting"
    READY = "ready"
    ERROR = "error"


@dataclass
class AsrStatusTracker:
    """ASR 状态与错误；修改它不会影响采集状态。"""

    since_ms: int = 0
    state: AsrState = AsrState.IDLE
    provider: str | None = None
    error_code: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.since_ms:
            self.since_ms = _now_ms()

    def connecting(self, provider: str | None = None) -> None:
        self.state = AsrState.CONNECTING
        self.provider = provider
        self.error_code = None
        self.error = None
        self.since_ms = _now_ms()

    def ready(self) -> None:
        self.state = AsrState.READY
        self.error_code = None
        self.error = None
        self.since_ms = _now_ms()

    def fail(self, code: str, message: str) -> None:
        self.state = AsrState.ERROR
        self.error_code = code
        self.error = message
        self.since_ms = _now_ms()

    def reset(self) -> None:
        self.state = AsrState.IDLE
        self.error_code = None
        self.error = None
        self.since_ms = _now_ms()

    def as_payload(self) -> dict[str, object]:
        """构造 ``asr.status`` 的 payload。"""
        return {
            "state": self.state.value,
            "since": self.since_ms,
            "provider": self.provider,
            "errorCode": self.error_code,
            "error": self.error,
        }


def _now_ms() -> int:
    return int(time.time() * 1000)
