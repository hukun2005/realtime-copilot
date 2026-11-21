"""按来源（loopback / mic）独立的音频统计。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .frame import AudioFrame
from .level import peak_dbfs, rms_dbfs

SOURCE_LOOPBACK = "loopback"
SOURCE_MIC = "mic"
SILENCE_DBFS = -50.0


@dataclass
class SourceStats:
    """单个来源的累计统计；不同来源绝不混合。"""

    frames_received: int = 0
    frames_dropped: int = 0
    duplicates: int = 0
    invalid: int = 0
    peak_dbfs: float | None = None
    rms_dbfs: float | None = None
    silence_duration_ms: int = 0
    last_frame_ts_ns: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    _energy_sum: float = field(default=0.0, repr=False)

    def on_frame(self, frame: AudioFrame) -> None:
        """记录一帧有效音频。"""
        level = peak_dbfs(frame.payload)
        frame_rms = rms_dbfs(frame.payload)
        self.frames_received += 1
        self.sample_rate = frame.sample_rate
        self.channels = frame.channels
        self.last_frame_ts_ns = frame.capture_ts_ns
        self.peak_dbfs = level if self.peak_dbfs is None else max(self.peak_dbfs, level)

        energy = 10 ** (frame_rms / 10.0)
        self._energy_sum += energy
        mean_energy = self._energy_sum / self.frames_received
        self.rms_dbfs = 10 * math.log10(mean_energy) if mean_energy > 0 else -120.0

        if level <= SILENCE_DBFS:
            self.silence_duration_ms += frame.frame_duration_ms
        else:
            self.silence_duration_ms = 0

    def on_dropped(self, reason: str) -> None:
        """记录一帧被丢弃；原因只影响对应计数。"""
        self.frames_dropped += 1
        if reason == "duplicate_sequence":
            self.duplicates += 1
        elif reason in {"invalid_frame", "timestamp_invalid", "timestamp_regression"}:
            self.invalid += 1

    def snapshot(self) -> dict[str, Any]:
        """``audio.status.sources`` / healthz 中的单来源片段。"""
        return {
            "framesReceived": self.frames_received,
            "framesDropped": self.frames_dropped,
            "duplicates": self.duplicates,
            "invalid": self.invalid,
            "peakDbfs": self.peak_dbfs,
            "rmsDbfs": self.rms_dbfs,
            "silenceDurationMs": self.silence_duration_ms,
            "lastFrameTimestamp": self.last_frame_ts_ns,
            "currentSampleRate": self.sample_rate,
            "currentChannels": self.channels,
        }
