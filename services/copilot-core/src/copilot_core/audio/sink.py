"""音频帧接收端（sink）。

Phase 2-A 的 sink 只做内存统计：不落盘、不保留 PCM、不写日志内容。
Phase 2-B 起由 VAD/ASR 实现替换，接口保持不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .frame import AudioFrame


class AudioFrameSink(Protocol):
    """接收已校验音频帧的消费者。"""

    def on_frame(self, frame: AudioFrame) -> None:
        """处理一帧。

        Args:
            frame: 已通过结构、序号与时间戳校验的帧。
        """


@dataclass(slots=True)
class InMemoryAudioSink:
    """只记录计数与最后时间戳的 sink。"""

    frames: int = 0
    bytes_received: int = 0
    last_frame_ts_ns: int | None = None
    last_source: str | None = None

    def on_frame(self, frame: AudioFrame) -> None:
        """累计一帧，但不保留 PCM。"""
        self.frames += 1
        self.bytes_received += len(frame.payload)
        self.last_frame_ts_ns = frame.capture_ts_ns
        self.last_source = frame.source
