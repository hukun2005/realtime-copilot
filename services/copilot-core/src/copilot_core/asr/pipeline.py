"""VAD -> ASR 输入队列的运行时接线。

音频会话把帧非阻塞地放进每来源有界队列；后台 worker 消费帧、运行 Segmenter，
再把完整 SpeechSegment 放进每来源有界 ASR 队列。ASR 慢不会拖慢采集。

内存预算：

- 单段 ``max_segment_bytes`` 上限；
- 全局 ``max_pcm_bytes_in_flight``（默认 32 MiB）上限；
- 消费或关闭时释放 PCM 引用并计数。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .vad import SOURCES, Segmenter, SpeechSegment, VadFrame


@dataclass
class PipelineStats:
    """队列、丢弃与 PCM 内存计数。"""

    frames_accepted: int = 0
    frames_dropped: int = 0
    segments_emitted: int = 0
    segments_dropped: int = 0
    vad_errors: int = 0
    worker_errors: int = 0
    pcm_bytes_queued: int = 0
    pcm_bytes_processing: int = 0
    pcm_bytes_in_flight: int = 0
    peak_pcm_bytes_in_flight: int = 0
    pcm_bytes_dropped: int = 0
    pcm_bytes_released: int = 0


class AsrPipeline:
    """每来源独立的 VAD 处理队列与有界 ASR 输入队列。"""

    def __init__(
        self,
        *,
        segmenter: Segmenter | None = None,
        queue_frames: int = 200,
        queue_segments: int = 8,
        max_segment_bytes: int = 1_000_000,
        max_pcm_bytes_in_flight: int = 32 * 1024 * 1024,
        max_segment_duration_ms: int = 30_000,
        on_segment: Callable[[SpeechSegment], None] | None = None,
    ) -> None:
        if queue_frames <= 0 or queue_segments <= 0:
            raise ValueError("queues must be positive")
        if max_segment_bytes <= 0 or max_pcm_bytes_in_flight <= 0:
            raise ValueError("PCM budgets must be positive")

        self._segmenter = segmenter or Segmenter(max_segment_ms=max_segment_duration_ms)
        self._queue_frames = queue_frames
        self._queue_segments = queue_segments
        self._max_segment_bytes = max_segment_bytes
        self._max_pcm_bytes = max_pcm_bytes_in_flight
        self._on_segment = on_segment
        # 队列在 start()（事件循环内）创建，避免在无循环的线程里绑定错误的事件循环。
        self._frame_queues: dict[str, asyncio.Queue[VadFrame]] = {}
        self._segment_queues: dict[str, asyncio.Queue[SpeechSegment]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = False
        self._processing: dict[int, int] = {}
        self.stats = PipelineStats()

    @property
    def segmenter(self) -> Segmenter:
        return self._segmenter

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> None:
        """启动每来源的消费任务；必须在事件循环内调用。"""
        if self._closed:
            raise RuntimeError("pipeline is closed")
        if self._tasks:
            return
        self._frame_queues = {
            source: asyncio.Queue(maxsize=self._queue_frames) for source in SOURCES
        }
        self._segment_queues = {
            source: asyncio.Queue(maxsize=self._queue_segments) for source in SOURCES
        }
        for source in SOURCES:
            self._tasks.append(asyncio.create_task(self._worker(source), name=f"vad-{source}"))

    def ingest(self, frame: VadFrame) -> None:
        """非阻塞入队；队列满时丢帧并计数，绝不抛异常。"""
        queue = self._frame_queues.get(frame.source)
        if queue is None or self._closed:
            self.stats.frames_dropped += 1
            return
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.stats.frames_dropped += 1

    async def _worker(self, source: str) -> None:
        queue = self._frame_queues[source]
        try:
            while True:
                frame = await queue.get()
                try:
                    events = self._segmenter.push(frame)
                except Exception:
                    # VAD 失败不能影响采集连接。
                    self.stats.vad_errors += 1
                    continue
                self.stats.frames_accepted += 1
                for event in events:
                    if event.segment is not None:
                        self._publish(event.segment)
        except asyncio.CancelledError:
            raise
        except Exception:
            # worker 异常必须可观察，并通过计数暴露给 healthz。
            self.stats.worker_errors += 1

    def _publish(self, segment: SpeechSegment) -> None:
        size = len(segment.pcm16)
        if size > self._max_segment_bytes:
            self.stats.pcm_bytes_dropped += size
            self.stats.segments_dropped += 1
            return
        if self.stats.pcm_bytes_in_flight + size > self._max_pcm_bytes:
            self.stats.pcm_bytes_dropped += size
            self.stats.segments_dropped += 1
            return

        queue = self._segment_queues[segment.source]
        try:
            queue.put_nowait(segment)
        except asyncio.QueueFull:
            self.stats.segments_dropped += 1
            self.stats.pcm_bytes_dropped += size
            return

        self.stats.segments_emitted += 1
        self.stats.pcm_bytes_queued += size
        self.stats.pcm_bytes_in_flight = self.stats.pcm_bytes_queued + self.stats.pcm_bytes_processing
        self.stats.peak_pcm_bytes_in_flight = max(
            self.stats.peak_pcm_bytes_in_flight, self.stats.pcm_bytes_in_flight
        )
        if self._on_segment is not None:
            try:
                self._on_segment(segment)
            except Exception:
                self.stats.vad_errors += 1

    def _take(self, source: str) -> SpeechSegment | None:
        queue = self._segment_queues.get(source)
        if queue is None:
            return None
        try:
            segment = queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        self._mark_processing(segment)
        return segment

    def _mark_processing(self, segment: SpeechSegment) -> None:
        size = len(segment.pcm16)
        self.stats.pcm_bytes_queued = max(0, self.stats.pcm_bytes_queued - size)
        self.stats.pcm_bytes_processing += size
        self.stats.pcm_bytes_in_flight = self.stats.pcm_bytes_queued + self.stats.pcm_bytes_processing
        self._processing[id(segment)] = size

    def release_segment(self, segment: SpeechSegment) -> None:
        """ASR 处理结束（成功/失败/取消/超时）后释放 PCM；幂等。"""
        size = self._processing.pop(id(segment), None)
        if size is None:
            return
        self.stats.pcm_bytes_processing = max(0, self.stats.pcm_bytes_processing - size)
        self.stats.pcm_bytes_in_flight = self.stats.pcm_bytes_queued + self.stats.pcm_bytes_processing
        self.stats.pcm_bytes_released += size

    def next_segment(self, source: str) -> SpeechSegment | None:
        """非阻塞取一个片段（测试/消费者用）；取出即释放预算。"""
        return self._take(source)

    async def get_segment(self, source: str) -> SpeechSegment:
        """等待一个片段；取出即释放预算。"""
        queue = self._segment_queues.get(source)
        if queue is None:
            raise RuntimeError("pipeline is not started")
        segment = await queue.get()
        self._mark_processing(segment)
        return segment

    async def stop_graceful(self, *, deadline_seconds: float = 5.0) -> None:
        """优雅停止：停止接收、取消 worker、drain 帧队列、flush 片段；保留已产出的片段。"""
        if self._closed:
            return
        self._closed = True
        await self._cancel_workers()
        await self._drain_and_flush()

    async def stop(self, *, deadline_seconds: float = 5.0) -> None:
        """``stop_graceful`` 的兼容别名。"""
        await self.stop_graceful(deadline_seconds=deadline_seconds)

    async def stop_immediate(self) -> None:
        """立即停止：不 flush，清空并释放所有排队 PCM。"""
        if self._closed:
            return
        self._closed = True
        await self._cancel_workers()
        for queue in self._frame_queues.values():
            while not queue.empty():
                queue.get_nowait()
        for queue in self._segment_queues.values():
            while not queue.empty():
                segment = queue.get_nowait()
                size = len(segment.pcm16)
                self.stats.pcm_bytes_queued = max(0, self.stats.pcm_bytes_queued - size)
                self.stats.pcm_bytes_released += size
        self.stats.pcm_bytes_processing = 0
        self.stats.pcm_bytes_released += sum(self._processing.values())
        self._processing.clear()
        self.stats.pcm_bytes_in_flight = 0

    async def close(self) -> None:
        """关闭并释放全部 PCM；可重复调用。"""
        await self.stop_immediate()
        self._segment_queues = {}
        self._frame_queues = {}

    async def _cancel_workers(self) -> None:
        tasks = list(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                self.stats.worker_errors += 1

    async def _drain_and_flush(self) -> None:
        for source in SOURCES:
            queue = self._frame_queues.get(source)
            if queue is None:
                continue
            while not queue.empty():
                frame = queue.get_nowait()
                try:
                    for event in self._segmenter.push(frame):
                        if event.segment is not None:
                            self._publish(event.segment)
                except Exception:
                    self.stats.vad_errors += 1
            for event in self._segmenter.flush(source):
                if event.segment is not None:
                    self._publish(event.segment)

    def snapshot(self) -> dict[str, Any]:
        """队列与统计快照（healthz 使用）。"""
        return {
            "framesAccepted": self.stats.frames_accepted,
            "framesDropped": self.stats.frames_dropped,
            "segmentsEmitted": self.stats.segments_emitted,
            "segmentsDropped": self.stats.segments_dropped,
            "vadErrors": self.stats.vad_errors,
            "workerErrors": self.stats.worker_errors,
            "queueDepth": sum(queue.qsize() for queue in self._frame_queues.values()),
            "segmentQueueDepth": sum(queue.qsize() for queue in self._segment_queues.values()),
            "pcmBytesQueued": self.stats.pcm_bytes_queued,
            "pcmBytesProcessing": self.stats.pcm_bytes_processing,
            "pcmBytesInFlight": self.stats.pcm_bytes_in_flight,
            "peakPcmBytesInFlight": self.stats.peak_pcm_bytes_in_flight,
            "pcmBytesDropped": self.stats.pcm_bytes_dropped,
            "pcmBytesReleased": self.stats.pcm_bytes_released,
            "maxPcmBytesInFlight": self._max_pcm_bytes,
            "maxSegmentBytes": self._max_segment_bytes,
            "ringBufferEvictions": {
                source: self._segmenter.ring_evictions(source) for source in SOURCES
            },
        }
