"""Deepgram 流式 ASR Provider（真实云端实现）。

官方文档：https://developers.deepgram.com/docs/streaming-audio — 使用 WebSocket，
PCM16 / 16kHz / 单声道，`interim_results=true` 返回 partial，`is_final` 定稿。

未持有 API Key 时无法在本机完成真实连接验证，因此本实现标记为 NOT_VERIFIED；
所有网络/认证失败都会转换成 ``AsrError``。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from .provider import AsrAuthError, AsrError, AsrProvider, AsrProviderConfig, AsrResult, env_api_key
from .vad import SpeechSegment

DEFAULT_URL = "wss://api.deepgram.com/v1/listen"


class DeepgramProvider(AsrProvider):
    """Deepgram 实时转写（每个 Segment 一条连接，保持实现简单可审计）。"""

    def __init__(self, config: AsrProviderConfig) -> None:
        self._config = config
        self._api_key = env_api_key(config)
        if not self._api_key:
            raise AsrAuthError(
                "asr_auth_missing",
                f"environment variable {config.api_key_env} is not set",
            )
        self._state = "idle"

    def status(self) -> str:
        return self._state

    async def connect(self, ticket: str) -> None:
        # 票据在本进程内已由 AsrManager 消费；这里只做凭证存在性检查。
        if not ticket:
            raise AsrAuthError("asr_auth_failed", "missing asr ticket")
        self._state = "ready"

    async def transcribe(self, segment: SpeechSegment) -> AsyncIterator[AsrResult]:
        if segment.sample_rate != self._config.sample_rate or segment.channels != self._config.channels:
            raise AsrError(
                "asr_failed",
                f"unsupported audio format: {segment.sample_rate}Hz/{segment.channels}ch",
            )

        try:
            import websockets
        except ImportError as error:  # pragma: no cover - 可选依赖
            raise AsrError("asr_unavailable", "websockets package is not installed") from error

        query = (
            "?encoding=linear16"
            f"&sample_rate={self._config.sample_rate}"
            f"&channels={self._config.channels}"
            "&interim_results=true"
            f"&language={self._config.language}"
        )
        uri = f"{DEFAULT_URL}{query}"

        try:
            async with websockets.connect(
                uri,
                additional_headers={"Authorization": f"Token {self._api_key}"},
                open_timeout=10,
            ) as socket:
                await socket.send(segment.pcm16)
                await socket.send(json.dumps({"type": "CloseStream"}))
                final_seen = False
                while not final_seen:
                    raw = await socket.recv()
                    message = json.loads(raw)
                    channel = message.get("channel") or {}
                    alternatives = channel.get("alternatives") or []
                    if not alternatives:
                        continue
                    alternative = alternatives[0]
                    text = (alternative.get("transcript") or "").strip()
                    if not text:
                        continue
                    is_final = bool(message.get("is_final") or message.get("speech_final"))
                    final_seen = is_final
                    yield AsrResult(
                        segment_id=segment.segment_id,
                        source=segment.source,
                        revision=0,
                        text=text,
                        start_ts_ns=segment.start_ts_ns,
                        end_ts_ns=segment.end_ts_ns,
                        final=is_final,
                        confidence=alternative.get("confidence"),
                    )
        except Exception as error:
            self._state = "error"
            raise AsrError("asr_failed", f"deepgram streaming failed: {error}", retryable=True) from error

    async def cancel(self) -> None:
        self._state = "ready"

    async def close(self) -> None:
        self._state = "idle"
