"""本地 VAD：能量阈值 + 片段切分。

设计约束：

- loopback 与 mic 各自维护独立状态，互不影响。
- 每个来源有一个**有界** Ring Buffer；只保留最近 N 帧，溢出即丢弃最旧帧并计数。
- 不缓存无上限音频，ASR 处理慢时不会无限积压。
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

SOURCE_LOOPBACK = "loopback"
SOURCE_MIC = "mic"
SOURCES = (SOURCE_LOOPBACK, SOURCE_MIC)


class VadEventKind(StrEnum):
    """VAD 事件类型。"""

    SPEECH_STARTED = "speech_started"
    SPEECH_ONGOING = "speech_ongoing"
    SPEECH_ENDED = "speech_ended"


@dataclass(frozen=True)
class SpeechSegment:
    """一段连续语音。"""

    segment_id: str
    source: str
    start_ts_ns: int
    end_ts_ns: int
    frames: int
    peak_dbfs: float
    rms_dbfs: float


@dataclass(frozen=True)
class VadEvent:
    """VAD 输出事件。"""

    kind: VadEventKind
    source: str
    ts_ns: int
    segment: SpeechSegment | None = None


class EnergyVad:
    """按 dBFS 阈值判断当前帧是否语音。"""

    def __init__(self, *, threshold_dbfs: float = -50.0) -> None:
        self._threshold = threshold_dbfs

    @property
    def threshold_dbfs(self) -> float:
        return self._threshold

    def is_speech(self, dbfs: float | None) -> bool:
        return dbfs is not None and dbfs >= self._threshold


@dataclass
class _SourceState:
    active: bool = False
    start_ts_ns: int = 0
    last_ts_ns: int = -1
    speech_ms: int = 0
    silence_ms: int = 0
    frames: int = 0
    peak_dbfs: float = -120.0
    sum_energy: float = 0.0
    segment_index: int = 0
    buffer: deque[tuple[int, float]] = field(default_factory=deque)
    dropped: int = 0


class Segmenter:
    """把逐帧能量切成语音片段，并为每个来源维护有界缓冲。"""

    def __init__(
        self,
        *,
        threshold_dbfs: float = -50.0,
        start_ms: int = 120,
        end_silence_ms: int = 400,
        frame_ms: int = 20,
        ring_max_frames: int = 1500,
    ) -> None:
        if frame_ms <= 0:
            raise ValueError("frame_ms must be positive")
        if ring_max_frames <= 0:
            raise ValueError("ring_max_frames must be positive")
        self._vad = EnergyVad(threshold_dbfs=threshold_dbfs)
        self._start_ms = start_ms
        self._end_silence_ms = end_silence_ms
        self._frame_ms = frame_ms
        self._ring_max_frames = ring_max_frames
        self._states: dict[str, _SourceState] = {
            source: _SourceState(buffer=deque(maxlen=ring_max_frames)) for source in SOURCES
        }

    @property
    def threshold_dbfs(self) -> float:
        return self._vad.threshold_dbfs

    def buffered_frames(self, source: str) -> int:
        return len(self._state(source).buffer)

    def dropped_frames(self, source: str) -> int:
        return self._state(source).dropped

    def is_active(self, source: str) -> bool:
        return self._state(source).active

    def push(self, source: str, dbfs: float, ts_ns: int) -> list[VadEvent]:
        """处理一帧；返回本次产生的 0..2 个事件。"""
        state = self._state(source)
        if ts_ns < state.last_ts_ns:
            raise ValueError(f"timestamp regression for {source}: {ts_ns} < {state.last_ts_ns}")
        state.last_ts_ns = ts_ns

        if len(state.buffer) == self._ring_max_frames:
            state.dropped += 1
        state.buffer.append((ts_ns, dbfs))

        speech = self._vad.is_speech(dbfs)
        events: list[VadEvent] = []

        if not state.active:
            state.speech_ms = state.speech_ms + self._frame_ms if speech else 0
            if speech and state.start_ts_ns == 0:
                state.start_ts_ns = ts_ns
            if state.speech_ms >= self._start_ms:
                state.active = True
                state.frames = 0
                state.peak_dbfs = -120.0
                state.sum_energy = 0.0
                state.silence_ms = 0
                state.segment_index += 1
                events.append(VadEvent(VadEventKind.SPEECH_STARTED, source, ts_ns))
            else:
                return events

        state.frames += 1
        state.peak_dbfs = max(state.peak_dbfs, dbfs)
        state.sum_energy += 10 ** (dbfs / 10.0)
        events.append(VadEvent(VadEventKind.SPEECH_ONGOING, source, ts_ns))

        state.silence_ms = 0 if speech else state.silence_ms + self._frame_ms
        if state.silence_ms >= self._end_silence_ms:
            segment = SpeechSegment(
                segment_id=f"seg_{source}_{state.segment_index}",
                source=source,
                start_ts_ns=state.start_ts_ns,
                end_ts_ns=ts_ns,
                frames=state.frames,
                peak_dbfs=round(state.peak_dbfs, 1),
                rms_dbfs=round(self._rms(state), 1),
            )
            events.append(VadEvent(VadEventKind.SPEECH_ENDED, source, ts_ns, segment))
            state.active = False
            state.speech_ms = 0
            state.start_ts_ns = 0

        return events

    def flush(self, source: str, ts_ns: int) -> list[VadEvent]:
        """强制结束当前片段（停止采集时调用）。"""
        state = self._state(source)
        if not state.active:
            return []
        segment = SpeechSegment(
            segment_id=f"seg_{source}_{state.segment_index}",
            source=source,
            start_ts_ns=state.start_ts_ns,
            end_ts_ns=max(ts_ns, state.last_ts_ns),
            frames=state.frames,
            peak_dbfs=round(state.peak_dbfs, 1),
            rms_dbfs=round(self._rms(state), 1),
        )
        state.active = False
        state.speech_ms = 0
        state.start_ts_ns = 0
        return [VadEvent(VadEventKind.SPEECH_ENDED, source, segment.end_ts_ns, segment)]

    def _state(self, source: str) -> _SourceState:
        try:
            return self._states[source]
        except KeyError as exc:
            raise ValueError(f"unknown audio source: {source}") from exc

    @staticmethod
    def _rms(state: _SourceState) -> float:
        if state.frames == 0:
            return -120.0
        mean_energy = state.sum_energy / state.frames
        if mean_energy <= 0:
            return -120.0
        return 10 * math.log10(mean_energy)
