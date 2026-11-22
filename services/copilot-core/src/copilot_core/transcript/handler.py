"""把 ASR final 接入装配、缓冲、持久化与 transcript 事件广播。

生命周期由调用方（capture 控制面）负责：

- ``start(session_id, generation)``：创建会话、重置装配/缓冲。
- ``handle(event)``：只处理 ``asr.final``，其余事件忽略。
- ``flush()``：产出装配器剩余 Segment。
- ``stop(reason)``：flush、结束会话、广播 ``transcript.reset``、清空缓冲。

处理失败不会向 ASR 链路抛出：记录计数并继续，避免转写故障导致采集中断。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from copilot_protocol import make_event

from ..storage import StorageError
from .assembler import TranscriptAssembler, TranscriptSegment
from .buffer import TranscriptBuffer
from .store import TranscriptStore

Broadcast = Callable[[Any], Awaitable[None]]


class TranscriptHandler:
    """ASR final → 装配 → 持久化 → `transcript.*`。"""

    def __init__(
        self,
        *,
        assembler: TranscriptAssembler,
        buffer: TranscriptBuffer,
        store: TranscriptStore,
        broadcast: Broadcast,
        logger: logging.Logger | None = None,
    ) -> None:
        self._assembler = assembler
        self._buffer = buffer
        self._store = store
        self._broadcast = broadcast
        self._logger = logger or logging.getLogger("copilot_core.transcript")
        self.session_id: str | None = None
        self.generation = assembler.generation
        self.assembled = 0
        self.persisted = 0
        self.broadcast_errors = 0

    @property
    def active(self) -> bool:
        return self.session_id is not None

    async def start(self, session_id: str, *, generation: int, mode: str = "standalone") -> None:
        """开始一次会话；已有会话先按 generation_changed 结束。"""
        if self.session_id is not None:
            await self.stop(reason="generation_changed")
        self.session_id = session_id
        self.generation = generation
        self._assembler.reset(generation)
        self._buffer.reset(generation)
        self._store.start_session(session_id, generation=generation, mode=mode)

    async def handle(self, event: Any) -> None:
        """处理一条 ASR 事件；只对 `asr.final` 生效。"""
        data = _wire(event)
        if data.get("type") != "asr.final" or self.session_id is None:
            return
        payload = data.get("payload") or {}
        try:
            emitted = self._assembler.accept(
                segment_id=payload["segmentId"],
                source=payload["source"],
                start_ts_ns=int(payload["startTsNs"]),
                end_ts_ns=int(payload["endTsNs"]),
                text=str(payload["text"]),
                revision=int(payload.get("revision", 1)),
                generation=self.generation,
                provider=payload.get("provider"),
            )
        except (KeyError, TypeError, ValueError):
            self._logger.warning("transcript: ignored malformed asr.final")
            return

        for segment in emitted:
            await self._emit(segment)

    async def flush(self) -> None:
        for segment in self._assembler.flush():
            await self._emit(segment)

    async def stop(self, *, reason: str = "capture_stopped") -> None:
        """结束会话；可重复调用。"""
        session_id = self.session_id
        if session_id is None:
            return
        await self.flush()
        self._store.end_session(session_id, status="completed")
        try:
            await self._broadcast(
                make_event(
                    "transcript.reset",
                    {
                        "sessionId": session_id,
                        "generation": self.generation,
                        "reason": reason,
                    },
                )
            )
        except Exception:
            self.broadcast_errors += 1
            self._logger.warning("transcript: reset broadcast failed")
        self._buffer.clear()
        self.session_id = None

    async def _emit(self, segment: TranscriptSegment) -> None:
        self.assembled += 1
        self._buffer.append(segment)
        try:
            inserted = self._store.append(self.session_id or "", segment)
        except StorageError:
            self._logger.warning("transcript: persist failed")
            return
        if not inserted:
            return
        self.persisted += 1
        try:
            await self._broadcast(
                make_event(
                    "transcript.segment",
                    {
                        "sessionId": self.session_id,
                        "segmentId": segment.segment_id,
                        "source": segment.source,
                        "startTsNs": segment.start_ts_ns,
                        "endTsNs": segment.end_ts_ns,
                        "text": segment.text,
                        "revision": segment.revision,
                        "generation": segment.generation,
                        "provider": segment.provider,
                    },
                )
            )
        except Exception:
            self.broadcast_errors += 1
            self._logger.warning("transcript: segment broadcast failed")


def _wire(event: Any) -> dict[str, Any]:
    if isinstance(event, BaseModel):
        return event.model_dump(by_alias=True, mode="json", exclude_none=True)
    if isinstance(event, dict):
        return event
    return {}
