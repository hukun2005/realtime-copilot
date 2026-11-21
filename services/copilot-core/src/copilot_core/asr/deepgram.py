"""Deepgram 流式 ASR Provider（Segment 级）。

官方接口（2026-11 核对）：
- WebSocket：``wss://api.deepgram.com/v1/listen``
- 音频：PCM16LE / 16kHz / 单声道，Binary frame
- ``interim_results=true`` 返回 partial；``is_final`` 表示文本块定稿；
  ``speech_final`` 表示 utterance 结束。
- 结束发送用 ``{"type": "CloseStream"}``；结果为 JSON（``channel.alternatives[0].transcript``）。

本实现把多个 ``is_final`` 文本块累积到 utterance，直到 ``speech_final`` 才产出 final，
并按 Segment 独立维护 revision。未持有 API Key 时真实连接无法验证（NOT_VERIFIED）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any

from .provider import AsrAuthError, AsrError, AsrProvider, AsrProviderConfig, AsrResult, env_api_key
from .vad import SpeechSegment

DEFAULT_URL = "wss://api.deepgram.com/v1/listen"


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
        self._active: set[Any] = set()
        self._closed = False
        self._cancelled = False
        self._state = "idle"

    def status(self) -> str:
        return self._state

    async def connect(self, ticket: str) -> None:
        if not ticket:
            raise AsrAuthError("asr_auth_failed", "missing asr ticket")
        self._state = "ready"

    def _query(self) -> str:
        options = {
            "encoding": "linear16",
            "sample_rate": str(self._config.sample_rate),
            "channels": str(self._config.channels),
            "interim_results": "true",
            "language": self._config.language,
            "punctuate": "true",
            "smart_format": "true",
        }
        if self._config.model:
            options["model"] = self._config.model
        options.update(self._config.options)
        return "&".join(f"{key}={value}" for key, value in options.items())

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
        self._cancelled = False
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
                    raise AsrError("asr_timeout", "deepgram receive timed out", retryable=True) from error

                message = _parse(raw)
                if message is None:
                    continue
                text = _transcript(message)
                is_final = bool(message.get("is_final"))
                speech_final = bool(message.get("speech_final"))

                if is_final and text:
                    committed = f"{committed}{text}" if committed else text

                if speech_final:
                    # 空文本的 speech_final 用已累积的定稿文本收尾。
                    if not committed:
                        continue
                    revision += 1
                    yield _result(segment, revision, committed, final=True)
                    await self._drain(socket)
                    return

                if not text:
                    continue

                if is_final:
                    # 文本块已定稿但 utterance 未结束：把累积文本作为新的 partial 暴露。
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
            self._state = "error"
            raise AsrError(
                "asr_connection_lost",
                f"deepgram connection failed: {error}",
                retryable=True,
            ) from error
        finally:
            await self._close_socket(socket)

    async def cancel(self) -> None:
        """关闭当前所有识别连接；幂等。"""
        self._cancelled = True
        for socket in list(self._active):
            with suppress(Exception):
                await socket.close()

    async def close(self) -> None:
        """关闭全部活动资源；幂等。"""
        if self._closed:
            return
        self._closed = True
        await self.cancel()
        self._state = "idle"

    async def _drain(self, socket: Any) -> None:
        """speech_final 之后短暂排空剩余消息，避免 CloseStream 结果残留。"""
        while True:
            try:
                await asyncio.wait_for(socket.recv(), timeout=0.2)
            except Exception:
                return

    async def _open_socket(self) -> Any:
        if self._connect is not None:
            connection = self._connect(
                f"{DEFAULT_URL}?{self._query()}",
                additional_headers={"Authorization": f"Token {self._api_key or 'test'}"},
                open_timeout=self._config.connect_timeout_seconds,
            )
        else:
            try:
                import websockets
            except ImportError as error:  # pragma: no cover - 可选依赖
                raise AsrError("asr_unavailable", "websockets package is not installed") from error
            connection = websockets.connect(
                f"{DEFAULT_URL}?{self._query()}",
                additional_headers={"Authorization": f"Token {self._api_key}"},
                open_timeout=self._config.connect_timeout_seconds,
            )

        try:
            socket = await connection
        except Exception as error:
            self._state = "error"
            raise AsrError("asr_unavailable", f"cannot connect deepgram: {error}") from error
        self._active.add(socket)
        self._state = "ready"
        return socket

    async def _close_socket(self, socket: Any) -> None:
        self._active.discard(socket)
        with suppress(Exception):
            await socket.close()


def _parse(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return message if isinstance(message, dict) else None


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


    async def _drain(self, socket: Any) -> None:
        """speech_final 之后短暂排空剩余消息，避免 CloseStream 结果残留。"""
        while True:
            try:
                await asyncio.wait_for(socket.recv(), timeout=0.2)
            except (TimeoutError, Exception):
                return
