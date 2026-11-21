"""Deepgram 流式 ASR Provider（Segment 级连接）。

状态语义：

- ``connect()`` 会建立一次**轻量验证连接**（打开后立即关闭），验证认证与网络；
  只有验证成功才进入 ``ready``，避免「假 READY」。
- ``transcribe()`` 每个 Segment 使用独立连接；单个 Segment 失败只记录请求级错误，
  不把 Provider 整体标记为断开。
- ``speech_final`` 后进入 settle 窗口：继续解析迟到的有效文本并累积，直到
  Metadata、连接关闭或 settle 超时，最后只产出一个 final，避免丢消息。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlencode

from .provider import AsrAuthError, AsrError, AsrProvider, AsrProviderConfig, AsrResult, env_api_key
from .vad import SpeechSegment

DEFAULT_URL = "wss://api.deepgram.com/v1/listen"

ALLOWED_OPTIONS = {
    "model",
    "language",
    "encoding",
    "sample_rate",
    "channels",
    "interim_results",
    "endpointing",
    "punctuate",
    "smart_format",
    "utterance_end_ms",
    "vad_events",
    "diarize",
    "multichannel",
    "profanity_filter",
    "redact",
    "replace",
    "search",
    "keywords",
    "tag",
    "version",
    "numerals",
    "filler_words",
    "dictation",
}


def _status_code(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    match = re.search(r"\b(4\d\d|5\d\d)\b", str(error))
    return int(match.group(1)) if match else None


class DeepgramProvider(AsrProvider):
    """Deepgram 实时转写；每个 Segment 一条连接。"""

    def __init__(
        self,
        config: AsrProviderConfig,
        *,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._api_key = env_api_key(config)
        if not self._api_key and connect is None:
            raise AsrAuthError(
                "asr_auth_missing",
                f"environment variable {config.api_key_env} is not set",
            )
        self._connect = connect
        self._state = "idle"
        self._closed = False
        self._closing = False
        self._active: set[Any] = set()
        self._semaphore = asyncio.Semaphore(max(1, config.max_concurrent_connections))
        self._metrics: dict[str, float] = {
            "connection_attempts": 0,
            "connection_successes": 0,
            "connection_failures": 0,
            "connection_duration_ms": 0.0,
            "segment_count": 0,
            "reconnect_count": 0,
            "rate_limited_count": 0,
            "timeout_count": 0,
            "provider_error_count": 0,
        }

    def status(self) -> str:
        return self._state

    def metrics(self) -> dict[str, float]:
        snapshot = dict(self._metrics)
        snapshot["active_web_sockets"] = len(self._active)
        return snapshot

    async def connect(self, ticket: str) -> None:
        """轻量验证连接：成功后才报告 READY。"""
        if self._closed:
            raise AsrError("asr_unavailable", "provider is closed")
        if not ticket:
            raise AsrAuthError("asr_auth_failed", "missing asr ticket")
        self._state = "connecting"
        try:
            socket = await self._open_socket(validate=True)
        except AsrError:
            self._state = "error"
            raise
        await self._close_socket(socket)
        self._state = "ready"

    async def transcribe(self, segment: SpeechSegment) -> AsyncIterator[AsrResult]:
        if segment.sample_rate != self._config.sample_rate or segment.channels != self._config.channels:
            raise AsrError(
                "asr_invalid_audio",
                f"unsupported audio format: {segment.sample_rate}Hz/{segment.channels}ch",
            )
        if not segment.pcm16:
            raise AsrError("asr_invalid_audio", "empty PCM segment")
        if self._closed:
            raise AsrError("asr_unavailable", "provider is closed")

        async with self._semaphore:
            self._metrics["segment_count"] += 1
            socket = await self._open_socket()
            committed = ""
            revision = 0
            try:
                await socket.send(segment.pcm16)
                await socket.send(json.dumps({"type": "CloseStream"}))
                while True:
                    try:
                        raw = await asyncio.wait_for(
                            socket.recv(), timeout=self._config.receive_timeout_seconds
                        )
                    except TimeoutError as error:
                        self._metrics["timeout_count"] += 1
                        raise AsrError("asr_timeout", "deepgram receive timed out", retryable=True) from error

                    message = _parse(raw)
                    if message is None:
                        continue
                    if _is_error(message):
                        self._metrics["provider_error_count"] += 1
                        raise AsrError(
                            "asr_provider_error",
                            str(message.get("description") or message.get("message") or "provider error"),
                        )

                    text = _transcript(message)
                    is_final = bool(message.get("is_final"))
                    speech_final = bool(message.get("speech_final"))

                    if text and is_final:
                        committed = f"{committed}{text}" if committed else text

                    if speech_final:
                        committed = await self._settle(socket, committed)
                        if not committed:
                            continue
                        revision += 1
                        yield _result(segment, revision, committed, final=True)
                        return

                    if not text:
                        continue
                    if is_final:
                        revision += 1
                        yield _result(segment, revision, committed, final=False)
                    else:
                        interim = f"{committed}{text}" if committed else text
                        revision += 1
                        yield _result(segment, revision, interim, final=False)
            except asyncio.CancelledError:
                raise
            except AsrError:
                raise
            except Exception as error:
                self._metrics["provider_error_count"] += 1
                self._state = "error" if self._state != "ready" else "ready"
                raise AsrError(
                    "asr_connection_lost",
                    f"deepgram connection failed: {error}",
                    retryable=True,
                ) from error
            finally:
                await self._close_socket(socket)

    async def _settle(self, socket: Any, committed: str) -> str:
        """speech_final 之后继续解析迟到消息，直到 Metadata/关闭/超时。"""
        deadline = asyncio.get_running_loop().time() + self._config.settle_timeout_seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return committed
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
            except TimeoutError:
                return committed
            except Exception:
                # 正常关闭或网络断开：已累积文本仍然有效。
                return committed

            message = _parse(raw)
            if message is None:
                continue
            if _is_error(message):
                self._metrics["provider_error_count"] += 1
                raise AsrError(
                    "asr_provider_error",
                    str(message.get("description") or message.get("message") or "provider error"),
                )
            if _is_terminal(message):
                return committed
            if bool(message.get("speech_final")) and _transcript(message):
                # 新的 utterance 不属于当前 Segment，停止并保留已累积文本。
                return committed
            text = _transcript(message)
            if text and bool(message.get("is_final")):
                committed = f"{committed}{text}" if committed else text

    async def cancel(self) -> None:
        """关闭当前所有识别连接；幂等。"""
        for socket in list(self._active):
            with contextlib.suppress(Exception):
                await socket.close()

    async def close(self) -> None:
        """关闭全部活动资源；幂等。"""
        if self._closed:
            return
        self._closing = True
        await self.cancel()
        self._closed = True
        self._state = "idle"

    def _query(self) -> str:
        options: dict[str, str] = {
            "encoding": "linear16",
            "sample_rate": str(self._config.sample_rate),
            "channels": str(self._config.channels),
            "interim_results": "true" if self._config.interim_results else "false",
            "language": self._config.language,
            "punctuate": "true" if self._config.punctuate else "false",
            "smart_format": "true" if self._config.smart_format else "false",
        }
        if self._config.endpointing_ms is not None:
            if self._config.endpointing_ms < 0:
                raise ValueError("endpointing_ms must be >= 0")
            options["endpointing"] = str(self._config.endpointing_ms)
        if self._config.model:
            options["model"] = self._config.model

        for key, value in self._config.options.items():
            if key not in ALLOWED_OPTIONS:
                raise ValueError(f"unsupported Deepgram option: {key}")
            if value is None or value == "":
                raise ValueError(f"Deepgram option {key} cannot be empty")
            options[key] = str(value)

        for key in ("language", "model"):
            if key in options and not options[key].strip():
                raise ValueError(f"Deepgram option {key} cannot be empty")
        return urlencode(options)

    async def _open_socket(self, *, validate: bool = False) -> Any:
        query = self._query()
        attempts = 1 if validate else max(1, self._config.max_retries + 1)
        last_error: Exception | None = None

        for attempt in range(attempts):
            started = time.monotonic()
            self._metrics["connection_attempts"] += 1
            try:
                connection = self._build_connection(query)
                socket = await connection
            except Exception as error:
                self._metrics["connection_failures"] += 1
                last_error = error
                status = _status_code(error)
                if status in (401, 403):
                    raise AsrAuthError("asr_auth_failed", "deepgram rejected the API key") from error
                if status == 429:
                    self._metrics["rate_limited_count"] += 1
                    if attempt < attempts - 1:
                        await self._backoff(attempt)
                        self._metrics["reconnect_count"] += 1
                        continue
                    raise AsrError("asr_rate_limited", "deepgram rate limit", retryable=True) from error
                if status is not None and 500 <= status < 600 and attempt < attempts - 1:
                    await self._backoff(attempt)
                    self._metrics["reconnect_count"] += 1
                    continue
                raise AsrError("asr_unavailable", f"cannot connect deepgram: {error}") from error

            self._metrics["connection_successes"] += 1
            self._metrics["connection_duration_ms"] += (time.monotonic() - started) * 1000
            self._active.add(socket)
            if self._state != "ready":
                self._state = "ready"
            return socket

        raise AsrError("asr_unavailable", f"cannot connect deepgram: {last_error}")

    def _build_connection(self, query: str) -> Any:
        url = f"{DEFAULT_URL}?{query}"
        headers = {"Authorization": f"Token {self._api_key or 'test'}"}
        if self._connect is not None:
            return self._connect(url, additional_headers=headers, open_timeout=self._config.connect_timeout_seconds)
        try:
            import websockets
        except ImportError as error:  # pragma: no cover - 可选依赖
            raise AsrError("asr_unavailable", "websockets package is not installed") from error
        return websockets.connect(
            url,
            additional_headers=headers,
            open_timeout=self._config.connect_timeout_seconds,
        )

    async def _backoff(self, attempt: int) -> None:
        base = self._config.backoff_base_seconds * (2**attempt)
        await asyncio.sleep(base + random.uniform(0, base / 2))

    async def _close_socket(self, socket: Any) -> None:
        self._active.discard(socket)
        with contextlib.suppress(Exception):
            await socket.close()


def _parse(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return message if isinstance(message, dict) else None


def _is_error(message: dict[str, Any]) -> bool:
    return str(message.get("type") or "").lower() == "error" or bool(message.get("error"))


def _is_terminal(message: dict[str, Any]) -> bool:
    kind = str(message.get("type") or "").lower()
    return kind in {"metadata", "utteranceend", "close"}


def _transcript(message: dict[str, Any]) -> str:
    channel = message.get("channel") or {}
    alternatives = channel.get("alternatives") or []
    if not alternatives:
        return ""
    return str(alternatives[0].get("transcript") or "").strip()


def _result(segment: SpeechSegment, revision: int, text: str, *, final: bool) -> AsrResult:
    return AsrResult(
        segment_id=segment.segment_id,
        source=segment.source,
        revision=revision,
        text=text,
        start_ts_ns=segment.start_ts_ns,
        end_ts_ns=segment.end_ts_ns,
        final=final,
    )
