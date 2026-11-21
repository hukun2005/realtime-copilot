"""本地 VAD：能量阈值 + 片段切分 + 完整 PCM 帧缓冲。

设计约束：

- loopback 与 mic 各自维护独立状态（Ring Buffer / Segmenter / 计数），互不影响。
- Ring Buffer 保存**完整帧**（含 PCM、时间戳、采样参数），默认 2 秒 pre-roll。
- Ring Buffer 覆盖最旧帧属于循环缓冲行为，单独计为 ``ring_buffer_evictions``；
  与 ASR 输入队列的丢弃是两回事。
- 片段包含 pre-roll 与起始确认阶段的帧；结束时间取最后一帧有效语音，
  结束静音单独记录，不计入有效语音时长。
- 超长语音按 ``max_segment_ms`` 切分，避免无限增长。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from ..pcm_level import peak_dbfs as pcm_peak_dbfs
from ..pcm_level import rms_dbfs as pcm_rms_dbfs

SOURCE_LOOPBACK = "loopback"
SOURCE_MIC = "mic"
SOURCES = (SOURCE_LOOPBACK, SOURCE_MIC)


class VadEventKind(StrEnum):
    """VAD 事件类型。"""

    SPEECH_STARTED = "speech_started"
    SPEECH_ONGOING = "speech_ongoing"
    SPEECH_ENDED = "speech_ended"


@dataclass(frozen=True)
class VadFrame:
    """送入 VAD 的一帧音频（保留 PCM 供 ASR 使用）。"""

    source: str
    sequence: int
    capture_ts_ns: int
    pcm16: bytes
    sample_rate: int
    channels: int
    frame_duration_ms: int
    peak_dbfs: float
    rms_dbfs: float


@dataclass(frozen=True)
class SpeechSegment:
    """一段连续语音；PCM 可直接交给 ASR provider。"""

    segment_id: str
    source: str
    start_ts_ns: int
    end_ts_ns: int
    frames: int
    pcm16: bytes
    sample_rate: int
    channels: int
    peak_dbfs: float
    rms_dbfs: float
    end_silence_ms: int = 0


@dataclass(frozen=True)
class VadEvent:
    """VAD 输出事件。"""

    kind: VadEventKind
    source: str
    ts_ns: int
    segment: SpeechSegment | None = None


class EnergyVad:
    """Peak + RMS 双指标能量 VAD，带噪声估计与起止滞回。

    - 起始判定使用 ``threshold_dbfs``；已激活时使用滞回后的更低阈值。
    - RMS 低于 ``rms_threshold_dbfs`` 的短促峰值视为冲击噪声，不当作语音。
    - 背景噪声用指数滑动平均估计，可选地把阈值抬到噪声底之上。
    """

    def __init__(
        self,
        *,
        threshold_dbfs: float = -50.0,
        rms_threshold_dbfs: float = -55.0,
        hysteresis_db: float = 6.0,
        noise_floor_dbfs: float = -75.0,
        adaptive_margin_db: float = 8.0,
        adaptive: bool = True,
    ) -> None:
        self._threshold = threshold_dbfs
        self._rms_threshold = rms_threshold_dbfs
        self._hysteresis = hysteresis_db
        self._noise_floor = noise_floor_dbfs
        self._adaptive_margin = adaptive_margin_db
        self._adaptive = adaptive
        self._noise_alpha = 0.05
        self.noise_triggers = 0

    @property
    def threshold_dbfs(self) -> float:
        return self._threshold

    @property
    def noise_floor_dbfs(self) -> float:
        return self._noise_floor

    @property
    def noise_triggered(self) -> int:
        return self.noise_triggers

    def current_threshold(self) -> float:
        """当前起始阈值；自适应时不低于噪声底 + 余量。"""
        if not self._adaptive:
            return self._threshold
        return max(self._threshold, self._noise_floor + self._adaptive_margin)

    def is_speech(
        self,
        peak_dbfs: float | None,
        rms_dbfs: float | None = None,
        *,
        active: bool = False,
    ) -> bool:
        """判断一帧是否语音。

        ``active=True`` 表示片段已开始，使用滞回后的阈值以避免抖动。
        """
        if peak_dbfs is None:
            return False
        threshold = self.current_threshold()
        if active:
            threshold -= self._hysteresis
        if peak_dbfs < threshold:
            return False
        if rms_dbfs is not None and rms_dbfs < self._rms_threshold:
            self.noise_triggers += 1
            return False
        return True

    def observe(self, peak_dbfs: float | None, speech: bool) -> None:
        """用非语音帧更新背景噪声估计。"""
        if peak_dbfs is None or speech or peak_dbfs < -120:
            return
        self._noise_floor = (1 - self._noise_alpha) * self._noise_floor + self._noise_alpha * peak_dbfs


@dataclass
class _SegmentState:
    active: bool = False
    frames: list[VadFrame] = field(default_factory=list)
    start_ts_ns: int = 0
    last_speech_ts_ns: int = 0
    last_speech_index: int = 0
    silence_ms: int = 0
    speech_ms: int = 0
    peak_dbfs: float = -120.0
    energy_sum: float = 0.0
    segment_index: int = 0
    emitted_silence_ms: int = 0


@dataclass
class _SourceState:
    ring: deque[VadFrame]
    ring_evictions: int = 0
    last_ts_ns: int = -1
    candidate_ms: int = 0
    current: _SegmentState = field(default_factory=_SegmentState)


class Segmenter:
    """把逐帧音频切成语音片段，并为每个来源维护有界 Ring Buffer。"""

    def __init__(
        self,
        *,
        threshold_dbfs: float = -50.0,
        start_ms: int = 120,
        end_silence_ms: int = 400,
        pre_roll_ms: int = 2000,
        max_segment_ms: int = 30_000,
        frame_ms: int = 20,
    ) -> None:
        if frame_ms <= 0:
            raise ValueError("frame_ms must be positive")
        if start_ms < 0 or end_silence_ms < 0 or pre_roll_ms < 0 or max_segment_ms <= 0:
            raise ValueError("invalid VAD windows")

        self._vad = EnergyVad(threshold_dbfs=threshold_dbfs)
        self._start_ms = start_ms
        self._end_silence_ms = end_silence_ms
        self._pre_roll_frames = max(1, pre_roll_ms // frame_ms)
        self._max_segment_ms = max_segment_ms
        self._frame_ms = frame_ms
        ring_capacity = self._pre_roll_frames + (start_ms // frame_ms) + 2
        self._states: dict[str, _SourceState] = {
            source: _SourceState(ring=deque(maxlen=ring_capacity)) for source in SOURCES
        }

    @property
    def threshold_dbfs(self) -> float:
        return self._vad.threshold_dbfs

    @property
    def noise_triggers(self) -> int:
        return self._vad.noise_triggered

    def current_threshold(self) -> float:
        return self._vad.current_threshold()

    def ring_evictions(self, source: str) -> int:
        return self._state(source).ring_evictions

    def buffered_frames(self, source: str) -> int:
        return len(self._state(source).ring)

    def is_active(self, source: str) -> bool:
        return self._state(source).current.active

    def push(self, frame: VadFrame) -> list[VadEvent]:
        """处理一帧；返回本次产生的事件（可能含结束与重新开始）。"""
        state = self._state(frame.source)
        if frame.capture_ts_ns < state.last_ts_ns:
            raise ValueError(
                f"timestamp regression for {frame.source}: {frame.capture_ts_ns} < {state.last_ts_ns}"
            )
        state.last_ts_ns = frame.capture_ts_ns

        if len(state.ring) == state.ring.maxlen:
            state.ring_evictions += 1
        state.ring.append(frame)

        speech = self._vad.is_speech(frame.peak_dbfs, frame.rms_dbfs, active=state.current.active)
        self._vad.observe(frame.peak_dbfs, speech)
        events: list[VadEvent] = []
        already_included = False

        if not state.current.active:
            state.candidate_ms = state.candidate_ms + frame.frame_duration_ms if speech else 0
            if state.candidate_ms < self._start_ms:
                return events
            self._begin_segment(state, frame.source)
            already_included = True
            events.append(VadEvent(VadEventKind.SPEECH_STARTED, frame.source, frame.capture_ts_ns))

        current = state.current
        if (frame.capture_ts_ns - current.start_ts_ns) >= self._max_segment_ms * 1_000_000:
            events.extend(self._finish_segment(state, frame.source, frame.capture_ts_ns, split=True))
            self._begin_segment(state, frame.source, include_ring=False)
            already_included = False

        current = state.current
        if not already_included:
            current.frames.append(frame)
            current.energy_sum += 10 ** (frame.peak_dbfs / 10.0)
            current.peak_dbfs = max(current.peak_dbfs, frame.peak_dbfs)
            if speech:
                current.last_speech_ts_ns = frame.capture_ts_ns
                current.last_speech_index = len(current.frames) - 1
                current.silence_ms = 0
            else:
                current.silence_ms += frame.frame_duration_ms
        elif speech:
            current.last_speech_ts_ns = frame.capture_ts_ns
            current.last_speech_index = len(current.frames) - 1

        events.append(VadEvent(VadEventKind.SPEECH_ONGOING, frame.source, frame.capture_ts_ns))

        if current.silence_ms >= self._end_silence_ms:
            events.extend(self._finish_segment(state, frame.source, frame.capture_ts_ns, split=False))

        return events

    def flush(self, source: str, ts_ns: int | None = None) -> list[VadEvent]:
        """强制结束当前片段（采集停止时调用）。"""
        state = self._state(source)
        if not state.current.active:
            return []
        end = ts_ns if ts_ns is not None else state.last_ts_ns
        return self._finish_segment(state, source, end, split=False)

    def _begin_segment(self, state: _SourceState, source: str, *, include_ring: bool = True) -> None:
        current = state.current
        current.segment_index += 1
        current.active = True
        # 启动时纳入 pre-roll + 起始确认帧；超长切分后的新段从当前帧重新开始。
        current.frames = list(state.ring) if include_ring else []
        if current.frames:
            current.start_ts_ns = current.frames[0].capture_ts_ns
        else:
            current.start_ts_ns = state.last_ts_ns
        current.last_speech_ts_ns = current.start_ts_ns
        current.last_speech_index = max(0, len(current.frames) - 1)
        current.silence_ms = 0
        current.speech_ms = 0
        current.peak_dbfs = max((frame.peak_dbfs for frame in current.frames), default=-120.0)
        current.energy_sum = sum(10 ** (frame.peak_dbfs / 10.0) for frame in current.frames)
        current.emitted_silence_ms = 0
        state.candidate_ms = 0

    def _finish_segment(
        self,
        state: _SourceState,
        source: str,
        ts_ns: int,
        *,
        split: bool,
    ) -> list[VadEvent]:
        current = state.current
        if not current.frames:
            current.active = False
            return []

        # 结束时间取最后一帧有效语音，结束静音不计入有效时长。
        kept = current.frames[: current.last_speech_index + 1]
        if not kept:
            kept = current.frames[-1:]
        pcm = b"".join(frame.pcm16 for frame in kept)
        # Peak / RMS 都基于最终 PCM，口径与采集侧电平统计一致。
        peak = pcm_peak_dbfs(pcm)
        rms = pcm_rms_dbfs(pcm)
        segment = SpeechSegment(
            segment_id=f"seg_{source}_{current.segment_index}",
            source=source,
            start_ts_ns=current.start_ts_ns,
            end_ts_ns=current.last_speech_ts_ns,
            frames=len(kept),
            pcm16=b"".join(frame.pcm16 for frame in kept),
            sample_rate=kept[-1].sample_rate,
            channels=kept[-1].channels,
            peak_dbfs=round(peak, 1),
            rms_dbfs=round(rms, 1),
            end_silence_ms=current.silence_ms,
        )
        current.active = False
        current.frames = []
        current.silence_ms = 0
        current.candidate_ms = 0
        _ = split
        return [VadEvent(VadEventKind.SPEECH_ENDED, source, ts_ns, segment)]

    def _state(self, source: str) -> _SourceState:
        try:
            return self._states[source]
        except KeyError as exc:
            raise ValueError(f"unknown audio source: {source}") from exc
