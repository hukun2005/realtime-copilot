"""ASR Provider 抽象、配置、注册表与测试用 Mock 实现。

Provider 不依赖 FastAPI / Electron / WebSocket 传输；结果通过异步迭代器返回。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from .vad import SpeechSegment


class AsrError(RuntimeError):
    """ASR 运行错误，``code`` 是稳定机器码。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class AsrAuthError(AsrError):
    """认证失败。"""


class AsrUnavailable(AsrError):
    """Provider 不可用（缺依赖、缺配置）。"""


@dataclass(frozen=True)
class AsrResult:
    """一条识别结果；partial 与 final 共用同一 segmentId。"""

    segment_id: str
    source: str
    revision: int
    text: str
    start_ts_ns: int
    end_ts_ns: int
    final: bool
    confidence: float | None = None


@dataclass
class AsrProviderConfig:
    """Provider 配置；API Key 只从环境变量读取。"""

    name: str = "mock"
    language: str = "zh-CN"
    sample_rate: int = 16000
    channels: int = 1
    model: str | None = None
    api_key_env: str = "ASR_API_KEY"
    options: dict[str, str] = field(default_factory=dict)
    receive_timeout_seconds: float = 15.0
    connect_timeout_seconds: float = 10.0
    interim_results: bool = True
    endpointing_ms: int | None = None
    punctuate: bool = True
    smart_format: bool = True
    settle_timeout_seconds: float = 0.5
    max_concurrent_connections: int = 2
    max_retries: int = 2
    backoff_base_seconds: float = 0.2


class AsrProvider(Protocol):
    """统一 Provider 接口。"""

    async def connect(self, ticket: str) -> None:
        """建立连接；``ticket`` 是一次性 ASR 票据。"""

    def transcribe(self, segment: SpeechSegment) -> AsyncIterator[AsrResult]:
        """识别一个语音片段，按顺序产出 partial/final。"""

    async def cancel(self) -> None:
        """取消当前识别。"""

    async def close(self) -> None:
        """关闭连接并释放资源。"""

    def status(self) -> str:
        """返回 ``idle`` / ``ready`` / ``error`` 之一。"""


class AsrRegistry:
    """Provider 工厂注册表。"""

    _factories: ClassVar[dict[str, Callable[[AsrProviderConfig], AsrProvider]]] = {}

    @classmethod
    def register(cls, name: str, factory: Callable[[AsrProviderConfig], AsrProvider]) -> None:
        cls._factories[name] = factory

    @classmethod
    def create(cls, config: AsrProviderConfig) -> AsrProvider:
        factory = cls._factories.get(config.name)
        if factory is None:
            raise AsrUnavailable(f"unknown ASR provider: {config.name}")
        return factory(config)

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._factories)


@dataclass
class MockAsrProvider:
    """测试用 Provider：可配置结果、延迟与失败。"""

    config: AsrProviderConfig = field(default_factory=AsrProviderConfig)
    results: list[AsrResult] = field(default_factory=list)
    delay_seconds: float = 0.0
    fail_connect: bool = False
    fail_auth: bool = False
    fail_transcribe: bool = False
    hang: bool = False
    connected: bool = False
    closed: bool = False
    cancelled: bool = False
    tickets: list[str] = field(default_factory=list)
    _state: str = "idle"

    async def connect(self, ticket: str) -> None:
        self.tickets.append(ticket)
        if self.fail_auth:
            self._state = "error"
            raise AsrAuthError("asr_auth_failed", "invalid api key")
        if self.fail_connect:
            self._state = "error"
            raise AsrUnavailable("asr_unavailable", "connect failed")
        self.connected = True
        self._state = "ready"

    async def transcribe(self, segment: SpeechSegment) -> AsyncIterator[AsrResult]:
        if self.fail_transcribe:
            raise AsrError("asr_failed", "transcribe failed")
        for result in self.results:
            if result.segment_id != segment.segment_id:
                continue
            if self.hang:
                await asyncio.sleep(3600)
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            yield result
            if result.final:
                return

    async def cancel(self) -> None:
        self.cancelled = True

    async def close(self) -> None:
        self.closed = True
        self._state = "idle"

    def status(self) -> str:
        return self._state


def env_api_key(config: AsrProviderConfig) -> str | None:
    """从环境变量读取 API Key；不落盘、不进日志。"""
    value = os.environ.get(config.api_key_env)
    return value.strip() if value else None
