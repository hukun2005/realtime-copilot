"""音频统计聚合：健康检查与日志的数据来源。"""

from __future__ import annotations

from typing import Any

from ..config import AudioConfig
from .session import AudioSession
from .sink import InMemoryAudioSink
from .stats import AudioCounters


class AudioHub:
    """进程级音频统计与会话工厂。"""

    def __init__(self, config: AudioConfig) -> None:
        self._config = config
        self.counters = AudioCounters()
        self.sink = InMemoryAudioSink()
        self._sessions = 0

    def open_session(self) -> AudioSession:
        """为一条新音频连接创建会话。

        Returns:
            共享本 hub 计数与 sink 的会话。
        """
        self._sessions += 1
        return AudioSession(
            counters=self.counters,
            sink=self.sink,
            max_frame_bytes=self._config.max_frame_bytes,
            max_frames_per_second=self._config.max_frames_per_second,
            burst_frames=self._config.burst_frames,
            max_invalid_per_second=self._config.max_invalid_per_second,
            max_rate_violations_per_second=self._config.max_rate_violations_per_second,
        )

    def close_session(self) -> None:
        """会话结束时递减活跃计数。"""
        self._sessions = max(0, self._sessions - 1)

    def snapshot(self) -> dict[str, Any]:
        """生成 ``/healthz`` 的 ``audio`` 片段。"""
        counters = self.counters
        return {
            "frames": counters.frames,
            "bytes": counters.bytes_received,
            "gaps": counters.gaps,
            "gapEvents": counters.gap_events,
            "duplicates": counters.duplicates,
            "invalid": counters.invalid,
            "tooLarge": counters.too_large,
            "rateLimited": counters.rate_limited,
            "timestampRegressions": counters.timestamp_regressions,
            "dropped": counters.dropped,
            "lastFrameTsNs": counters.last_frame_ts_ns,
            "errorCodes": dict(counters.error_codes),
            "sessions": self._sessions,
            "sinkFrames": self.sink.frames,
        }

    @property
    def active_sessions(self) -> int:
        """当前活跃的音频连接数。"""
        return self._sessions
