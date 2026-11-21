"""ASR 运行时：Provider 生命周期、Segment 消费、结果校验与事件广播。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from copilot_protocol import make_asr_error, make_asr_result, make_asr_status

from ..tickets import TicketIssuer
from .provider import AsrError, AsrProvider, AsrResult
from .state import AsrState, AsrStatusTracker
from .stream import AsrStreamError, AsrStreamTracker
from .vad import SOURCES, SpeechSegment


class AsrManager:
    """管理一个采集会话的 ASR：每来源串行消费、结果校验、状态广播。"""

    def __init__(
        self,
        *,
        pipeline: Any,
        tickets: TicketIssuer,
        provider: AsrProvider,
        generation: int,
        broadcast: Callable[[Any], Awaitable[None]],
        segment_timeout_seconds: float = 15.0,
        tracker: AsrStatusTracker | None = None,
        stream_tracker: AsrStreamTracker | None = None,
        logger: Any = None,
        current_generation: Callable[[], int] | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._tickets = tickets
        self._provider = provider
        self._generation = generation
        self._broadcast = broadcast
        self._segment_timeout = segment_timeout_seconds
        self._tracker = tracker or AsrStatusTracker()
        self._streams = stream_tracker or AsrStreamTracker()
        self._logger = logger
        self._tasks: list[asyncio.Task[None]] = []
        self._active = False
        self._partials = 0
        self._finals = 0
        self._errors = 0
        self._error_codes: dict[str, int] = {}
        self._by_source: dict[str, dict[str, int]] = {}
        self._current_generation = current_generation

    @property
    def state(self) -> AsrState:
        return self._tracker.state

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def active(self) -> bool:
        return self._active

    def snapshot(self) -> dict[str, Any]:
        """``/healthz`` 的 ``asr`` 片段。"""
        return {
            "state": self._tracker.state.value,
            "since": self._tracker.since_ms,
            "provider": self._tracker.provider,
            "generation": self._generation,
            "errorCode": self._tracker.error_code,
            "error": self._tracker.error,
            "partials": self._partials,
            "finals": self._finals,
            "errors": self._errors,
            "errorCodes": dict(self._error_codes),
            "bySource": {key: dict(value) for key, value in self._by_source.items()},
        }

    async def start(self) -> bool:
        """连接 Provider 并启动每来源消费者；失败进入 ERROR 并返回 False。"""
        if self._active:
            return True

        self._tracker.connecting(type(self._provider).__name__)
        await self._publish_status()

        try:
            if (
                self._current_generation is not None
                and self._current_generation() != self._generation
            ):
                raise AsrError("asr_stale_generation", "capture session has moved on")
            ticket = self._tickets.issue("asr", generation=self._generation)
            check = self._tickets.consume(ticket, purpose="asr", generation=self._generation)
            if not check.ok:
                raise AsrError("ticket_invalid", f"asr ticket rejected: {check.reason}")
            await self._provider.connect(ticket)
        except AsrError as error:
            self._tracker.fail(error.code, str(error))
            await self._publish_status()
            return False
        except Exception as error:  # Provider 的非预期失败也要归类
            self._tracker.fail("asr_failed", str(error))
            await self._publish_status()
            return False

        self._active = True
        self._tracker.ready()
        await self._publish_status()
        for source in SOURCES:
            self._tasks.append(asyncio.create_task(self._consume(source), name=f"asr-{source}"))
        return True

    async def stop(self) -> None:
        """停止消费者、取消 Provider 并回到 IDLE；可重复调用。"""
        if not self._active and not self._tasks:
            self._tracker.reset()
            with suppress(Exception):
                await self._provider.close()
            return
        self._active = False
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as error:
                if self._logger is not None:
                    self._logger.warning("asr consumer failed: %s", error)
        with suppress(Exception):
            await self._provider.close()
        self._tracker.reset()
        await self._publish_status()

    async def cancel_current(self) -> None:
        """取消当前识别（不改变状态）。"""
        with suppress(Exception):
            await self._provider.cancel()

    async def _consume(self, source: str) -> None:
        while self._active:
            try:
                segment = await asyncio.wait_for(self._pipeline.get_segment(source), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            except Exception:
                return

            if not self._active:
                return
            await self._transcribe(segment)

    async def _transcribe(self, segment: SpeechSegment) -> None:
        try:
            async with asyncio.timeout(self._segment_timeout):
                async for result in self._provider.transcribe(segment):
                    if not self._active:
                        return
                    await self._accept(result, segment)
        except TimeoutError:
            await self._publish_error("asr_timeout", "asr transcription timed out", segment.segment_id)
        except AsrError as error:
            if self._active:
                await self._publish_error(error.code, str(error), segment.segment_id, error.retryable)
        except Exception as error:
            if self._active:
                await self._publish_error("asr_provider_error", str(error), segment.segment_id)
        finally:
            release = getattr(self._pipeline, "release_segment", None)
            if release is not None:
                release(segment)

    async def _accept(self, result: AsrResult, segment: SpeechSegment) -> None:
        if not self._active:
            return
        if (
            self._current_generation is not None
            and self._current_generation() != self._generation
        ):
            await self._publish_error(
                "asr_stale_generation", "result belongs to an older session", segment.segment_id
            )
            return
        if result.segment_id != segment.segment_id or result.source != segment.source:
            await self._publish_error(
                "asr_provider_error", "provider returned a mismatched segment", segment.segment_id
            )
            return

        try:
            if result.final:
                self._streams.accept_final(
                    result.segment_id,
                    result.revision,
                    segment.start_ts_ns,
                    segment.end_ts_ns,
                    result.text,
                )
            else:
                self._streams.accept_partial(
                    result.segment_id,
                    result.revision,
                    segment.start_ts_ns,
                    segment.end_ts_ns,
                    result.text,
                )
        except AsrStreamError as error:
            await self._publish_error("asr_segment_conflict", str(error), segment.segment_id)
            return

        stats = self._by_source.setdefault(result.source, {"partials": 0, "finals": 0})
        if result.final:
            self._finals += 1
            stats["finals"] += 1
        else:
            self._partials += 1
            stats["partials"] += 1
        event = make_asr_result(
            "asr.final" if result.final else "asr.partial",
            segment_id=result.segment_id,
            source=result.source,
            start_ts_ns=segment.start_ts_ns,
            end_ts_ns=segment.end_ts_ns,
            revision=result.revision,
            text=result.text,
            confidence=result.confidence,
            provider=type(self._provider).__name__,
        )
        await self._broadcast(event)

    async def _publish_error(
        self,
        code: str,
        message: str,
        segment_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        self._errors += 1
        self._error_codes[code] = self._error_codes.get(code, 0) + 1
        self._error_codes[code] = self._error_codes.get(code, 0) + 1
        await self._broadcast(
            make_asr_error(code=code, message=message, segment_id=segment_id, retryable=retryable)
        )

    async def _publish_status(self) -> None:
        await self._broadcast(
            make_asr_status(
                state=self._tracker.state.value,
                since=self._tracker.since_ms,
                provider=self._tracker.provider,
                error_code=self._tracker.error_code,
                error=self._tracker.error,
            )
        )
