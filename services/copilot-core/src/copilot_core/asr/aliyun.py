"""阿里云百炼 Paraformer 实时 ASR Provider（Segment 级 WebSocket）。

协议（官方文档）：
- ``wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference``
- 握手头 ``Authorization: Bearer <DASHSCOPE_API_KEY>``
- 客户端发 ``run-task`` → 二进制 PCM → ``finish-task``
- 服务端返回 ``task-started`` / ``result-generated`` / ``task-finished`` / ``task-failed``
- ``result-generated`` 的 ``payload.output.sentence`` 包含 ``text`` 与 ``sentence_end``；
  ``sentence_end=false`` 为 partial，``true`` 为 final。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from .provider import AsrAuthError, AsrError, AsrProvider, AsrProviderConfig, AsrResult, env_api_key
from .vad import SpeechSegment

DEFAULT_MODEL = "paraformer-realtime-v2"


class DashScopeParaformerProvider(AsrProvider):
    """百炼 Paraformer 实时识别；每个 Segment 一条 WebSocket。"""

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
        self._active: set[Any] = set()
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
        data = dict(self._metrics)
        data["active_web_sockets"] = len(self._active)
        return data

    @property
    def url(self) -> str:
        workspace = self._config.workspace_id
        if not workspace:
            raise ValueError("workspace_id is required for Aliyun Paraformer")
        return f"wss://{workspace}.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"

    async def connect(self, ticket: str) -> None:
        """用 100ms 静音做一次真实握手验证，成功后才 READY。"""
        if self._closed:
            raise AsrError("asr_unavailable", "provider is closed")
        if not ticket:
            raise AsrAuthError("asr_auth_failed", "missing asr ticket")
        self._state = "connecting"
        try:
            socket = await self._open_socket()
        except AsrError:
            self._state = "error"
            raise
        try:
            await self._run_task(socket)
            await self._send_audio(socket, b"\x00\x00" * 1600)  # 100ms 静音
            await self._finish_task(socket)
            await self._wait_terminal(socket, deadline_seconds=10.0)
        finally:
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

        self._metrics["segment_count"] += 1
        socket = await self._open_socket()
        revision = 0
        committed = ""
        try:
            await self._run_task(socket)
            await self._send_audio(socket, segment.pcm16)
            await self._finish_task(socket)

            while True:
                message = await self._receive(socket)
                event = _event(message)
                if event == "task-failed":
                    self._metrics["provider_error_count"] += 1
                    raise AsrError("asr_provider_error", _error_text(message))
                if event == "task-finished":
                    # 一个 SpeechSegment 只能有一个 final；多句在此合并。
                    if committed:
                        revision += 1
                        yield AsrResult(
                            segment_id=segment.segment_id,
                            source=segment.source,
                            revision=revision,
                            text=committed,
                            start_ts_ns=segment.start_ts_ns,
                            end_ts_ns=segment.end_ts_ns,
                            final=True,
                        )
                    return

                sentence = _sentence(message)
                if not sentence:
                    continue
                text = str(sentence.get("text") or "").strip()
                if not text:
                    continue
                if sentence.get("sentence_end"):
                    committed = f"{committed}{text}" if committed else text
                    text = committed
                revision += 1
                yield AsrResult(
                    segment_id=segment.segment_id,
                    source=segment.source,
                    revision=revision,
                    text=text,
                    start_ts_ns=segment.start_ts_ns,
                    end_ts_ns=segment.end_ts_ns,
                    final=False,
                )
        except asyncio.CancelledError:
            raise
        except AsrError:
            raise
        except Exception as error:
            self._metrics["provider_error_count"] += 1
            raise AsrError(
                "asr_connection_lost",
                f"aliyun paraformer connection failed: {error}",
                retryable=True,
            ) from error
        finally:
            await self._close_socket(socket)

    async def cancel(self) -> None:
        for socket in list(self._active):
            with contextlib.suppress(Exception):
                await socket.close()

    async def close(self) -> None:
        if self._closed:
            return
        await self.cancel()
        self._closed = True
        self._state = "idle"

    async def _open_socket(self) -> Any:
        url = self.url  # 配置缺失时直接抛出 ValueError，不包装成连接错误
        started = time.monotonic()
        self._metrics["connection_attempts"] += 1
        try:
            if self._connect is not None:
                connection = self._connect(url, additional_headers=self._headers())
            else:
                import websockets

                connection = websockets.connect(
                    url,
                    additional_headers=self._headers(),
                    open_timeout=self._config.connect_timeout_seconds,
                )
            socket = await connection
        except Exception as error:
            self._metrics["connection_failures"] += 1
            status = _status_code(error)
            if status in (401, 403):
                raise AsrAuthError("asr_auth_failed", "aliyun rejected the API key") from error
            if status == 429:
                self._metrics["rate_limited_count"] += 1
                raise AsrError("asr_rate_limited", "aliyun rate limit", retryable=True) from error
            raise AsrError("asr_unavailable", f"cannot connect aliyun: {error}") from error

        self._metrics["connection_successes"] += 1
        self._metrics["connection_duration_ms"] += (time.monotonic() - started) * 1000
        self._active.add(socket)
        return socket

    async def _close_socket(self, socket: Any) -> None:
        self._active.discard(socket)
        with contextlib.suppress(Exception):
            await socket.close()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key or 'test'}"}

    async def _run_task(self, socket: Any) -> None:
        task = str(uuid.uuid4())
        socket.task_id = task  # type: ignore[attr-defined]
        await socket.send(json.dumps(self._run_payload(task)))
        message = await self._receive(socket)
        event = _event(message)
        if event == "task-failed":
            raise AsrError("asr_provider_error", _error_text(message))
        if event != "task-started":
            raise AsrError("asr_provider_error", f"unexpected event: {event}")

    async def _finish_task(self, socket: Any) -> None:
        task = getattr(socket, "task_id", None)
        await socket.send(json.dumps(self._finish_payload(task)))

    async def _send_audio(self, socket: Any, pcm: bytes) -> None:
        step = 3200  # 100ms @16k mono
        for index in range(0, len(pcm), step):
            await socket.send(pcm[index : index + step])
            await asyncio.sleep(0.01)

    async def _receive(self, socket: Any) -> dict[str, Any]:
        try:
            raw = await asyncio.wait_for(
                socket.recv(), timeout=self._config.receive_timeout_seconds
            )
        except TimeoutError as error:
            self._metrics["timeout_count"] += 1
            raise AsrError("asr_timeout", "aliyun receive timed out", retryable=True) from error
        return _parse(raw)

    async def _wait_terminal(self, socket: Any, *, deadline_seconds: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + deadline_seconds
        while loop.time() < deadline:
            try:
                message = await asyncio.wait_for(socket.recv(), timeout=max(0.0, deadline - loop.time()))
            except Exception:
                return
            event = _event(_parse(message))
            if event in ("task-finished", "task-failed"):
                return


    def _run_payload(self, task: str) -> dict[str, Any]:
        language = self._config.language.split("-")[0] or "zh"
        return {
            "header": {"action": "run-task", "task_id": task, "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self._config.model or DEFAULT_MODEL,
                "parameters": {
                    "format": "pcm",
                    "sample_rate": self._config.sample_rate,
                    "language_hints": [language],
                    "semantic_punctuation_enabled": False,
                    "max_sentence_silence": 800,
                },
                "input": {},
            },
        }

    def _finish_payload(self, task: str | None) -> dict[str, Any]:
        return {
            "header": {
                "action": "finish-task",
                "task_id": task or str(uuid.uuid4()),
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }


def _parse(raw: Any) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _event(message: dict[str, Any]) -> str:
    return str((message.get("header") or {}).get("event") or "")


def _error_text(message: dict[str, Any]) -> str:
    header = message.get("header") or {}
    return str(header.get("error_message") or header.get("error_code") or "aliyun task failed")


def _sentence(message: dict[str, Any]) -> dict[str, Any] | None:
    payload = message.get("payload") or {}
    output = payload.get("output") or {}
    sentence = output.get("sentence")
    return sentence if isinstance(sentence, dict) else None


def _status_code(error: Exception) -> int | None:
    code = getattr(getattr(error, "response", None), "status_code", None)
    return code if isinstance(code, int) else None
