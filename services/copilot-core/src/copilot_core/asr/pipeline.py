"""VAD -> ASR 输入队列的运行时接线。

音频会话把帧非阻塞地放进每来源有界队列；后台 worker 消费帧、运行 Segmenter，
再把完整 SpeechSegment 放进每来源有界 ASR 队列。ASR 慢不会拖慢采集。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .vad import SOURCES, Segmenter, SpeechSegment, VadFrame


@dataclass
class PipelineStats:
    """队列与丢弃计数。"""

    frames_accepted: int = 0
    frames_dropped: int = 0
    segments_emitted: int = 0
    segments_dropped: int = 0
    vad_errors: int = 0
    queue_depth: int = 0


class AsrPipeline:
    """每来源独立的 VAD 处理队列与 ASR 输入队列。"""

    def __init__(
        self,
        *,
        segmenter: Segmenter | None = None,
        queue_frames: int = 200,
        queue_segments: int = 8,
        on_segment: Callable[[SpeechSegment], None] | None = None,
    ) -> None:
        if queue_frames <= 0 or queue_segments <= 0:
            raise ValueError("queues must be positive")
        self._segmenter = segmenter or Segmenter()
        self._queue_frames = queue_frames
        self._queue_segments = queue_segments
        self._on_segment = on_segment
        # 队列在 start()（事件循环内）创建，避免在无循环的线程里绑定错误的事件循环。
        self._frame_queues: dict[str, asyncio.Queue[VadFrame]] = {}
        self._segment_queues: dict[str, asyncio.Queue[SpeechSegment]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self.stats = PipelineStats()

    @property
    def segmenter(self) -> Segmenter:
        return self._segmenter

    def start(self) -> None:
        """启动每来源的消费任务；必须在事件循环内调用。"""
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
        if queue is None:
            return
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:
            self.stats.frames_dropped += 1

    async def _worker(self, source: str) -> None:
        queue = self._frame_queues[source]
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

    def _publish(self, segment: SpeechSegment) -> None:
        queue = self._segment_queues[segment.source]
        try:
            queue.put_nowait(segment)
        except asyncio.QueueFull:
            self.stats.segments_dropped += 1
            return
        self.stats.segments_emitted += 1
        if self._on_segment is not None:
            try:
                self._on_segment(segment)
            except Exception:
                self.stats.vad_errors += 1

    async def stop(self) -> None:
        """停止 worker，flush 剩余片段；队列满时按丢弃策略计数。"""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                self.stats.vad_errors += 1
        self._tasks.clear()

        # 先清空帧队列，让 Segmenter 处理完剩余帧，再 flush。
        for source in SOURCES:
            queue = self._frame_queues[source]
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

    def next_segment(self, source: str) -> SpeechSegment | None:
        """非阻塞取一个片段（测试/消费者用）。"""
        queue = self._segment_queues.get(source)
        if queue is None:
            return None
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def get_segment(self, source: str) -> SpeechSegment:
        """等待一个片段。"""
        queue = self._segment_queues.get(source)
        if queue is None:
            raise RuntimeError("pipeline is not started")
        return await queue.get()

    def snapshot(self) -> dict[str, Any]:
        """队列与统计快照。"""
        return {
            "framesAccepted": self.stats.frames_accepted,
            "framesDropped": self.stats.frames_dropped,
            "segmentsEmitted": self.stats.segments_emitted,
            "segmentsDropped": self.stats.segments_dropped,
            "vadErrors": self.stats.vad_errors,
            "queueDepth": sum(queue.qsize() for queue in self._frame_queues.values()),
            "segmentQueueDepth": sum(queue.qsize() for queue in self._segment_queues.values()),
            "ringBufferEvictions": {
                source: self._segmenter.ring_evictions(source) for source in SOURCES
            },
        }
