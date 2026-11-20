"""单条音频连接的状态：序号连续性、时间戳、速率限制与统计。

设计取舍（与 ``audio-frame.v1.md`` §5–§7 一致）：

- 帧级错误只丢弃并计数，不关闭连接；音频是实时流，丢一帧优于断流。
- 连接级违规（超大帧、持续刷错误帧、持续超速）才断开，阈值可配。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .frame import PAYLOAD_BYTES, FrameError, parse_frame
from .level import peak_dbfs, rms_dbfs
from .sink import AudioFrameSink
from .stats import AudioCounters

SEQUENCE_WINDOW = 1 << 32
SEQUENCE_HALF = 1 << 31
TIMESTAMP_REGRESSION_NS = 1_000_000_000
VIOLATION_WINDOW_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class FrameOutcome:
    """一帧的处理结果。"""

    accepted: bool
    code: str | None = None


@dataclass(slots=True)
class _SourceState:
    """单个 source 流的连续性状态。"""

    last_sequence: int | None = None
    last_ts_ns: int | None = None


class _TokenBucket:
    """按帧计数的令牌桶，用于限制发送速率。"""

    __slots__ = ("_capacity", "_last", "_rate", "_tokens")

    def __init__(self, rate: float, burst: float, now: float) -> None:
        self._rate = rate
        self._capacity = burst
        self._tokens = burst
        self._last = now

    def take(self, now: float) -> bool:
        """消耗一个令牌；返回是否允许。"""
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class AudioSession:
    """一条 ``role=audio`` 连接上的音频会话。"""

    def __init__(
        self,
        *,
        counters: AudioCounters,
        sink: AudioFrameSink,
        max_frame_bytes: int,
        max_frames_per_second: int,
        burst_frames: int,
        max_invalid_per_second: int = 25,
        max_rate_violations_per_second: int = 10,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._counters = counters
        self._sink = sink
        self._max_frame_bytes = max_frame_bytes
        self._max_invalid = max_invalid_per_second
        self._max_rate_violations = max_rate_violations_per_second
        self._now = now
        self._bucket = _TokenBucket(float(max_frames_per_second), float(burst_frames), now())
        self._sources: dict[str, _SourceState] = {}
        self._window_started = now()
        self._invalid_in_window = 0
        self._rate_violations_in_window = 0

    def handle(self, data: bytes) -> FrameOutcome:
        """处理一条二进制消息。

        Args:
            data: WebSocket 二进制消息内容。

        Returns:
            是否接受，以及被丢弃时的原因码。
        """
        now = self._now()
        self._roll_window(now)

        if len(data) > self._max_frame_bytes:
            self._counters.too_large += 1
            self._counters.dropped += 1
            return FrameOutcome(False, "frame_too_large")

        if not self._bucket.take(now):
            self._counters.rate_limited += 1
            self._counters.dropped += 1
            self._rate_violations_in_window += 1
            return FrameOutcome(False, "rate_limited")

        try:
            frame = parse_frame(data)
        except FrameError as error:
            self._counters.count_error(error.code)
            self._counters.dropped += 1
            self._invalid_in_window += 1
            return FrameOutcome(False, error.code)

        state = self._sources.setdefault(frame.source, _SourceState())

        if state.last_sequence is not None:
            delta = (frame.sequence - state.last_sequence) % SEQUENCE_WINDOW
            if delta == 0 or delta >= SEQUENCE_HALF:
                self._counters.duplicates += 1
                self._counters.dropped += 1
                return FrameOutcome(False, "duplicate_sequence")
            if delta > 1:
                self._counters.gaps += delta - 1
                self._counters.gap_events += 1
                self._counters.last_gap_sequence = frame.sequence

        if frame.capture_ts_ns == 0:
            self._counters.count_error("timestamp_invalid")
            self._counters.dropped += 1
            self._invalid_in_window += 1
            return FrameOutcome(False, "timestamp_invalid")

        if state.last_ts_ns is not None and frame.capture_ts_ns + TIMESTAMP_REGRESSION_NS < state.last_ts_ns:
            self._counters.timestamp_regressions += 1
            self._counters.dropped += 1
            return FrameOutcome(False, "timestamp_regression")

        state.last_sequence = frame.sequence
        state.last_ts_ns = frame.capture_ts_ns
        self._counters.frames += 1
        self._counters.bytes_received += len(frame.payload)
        self._counters.last_frame_ts_ns = frame.capture_ts_ns
        level = peak_dbfs(frame.payload)
        self._counters.last_peak_dbfs = level
        if self._counters.peak_dbfs is None or level > self._counters.peak_dbfs:
            self._counters.peak_dbfs = level
        self._counters.last_rms_dbfs = rms_dbfs(frame.payload)
        self._sink.on_frame(frame)
        return FrameOutcome(True)

    def should_disconnect(self) -> bool:
        """当前窗口内的违规是否已达断开阈值。"""
        return (
            self._invalid_in_window > self._max_invalid
            or self._rate_violations_in_window > self._max_rate_violations
        )

    def _roll_window(self, now: float) -> None:
        if now - self._window_started > VIOLATION_WINDOW_SECONDS:
            self._window_started = now
            self._invalid_in_window = 0
            self._rate_violations_in_window = 0


__all__ = ["PAYLOAD_BYTES", "AudioSession", "FrameOutcome"]
