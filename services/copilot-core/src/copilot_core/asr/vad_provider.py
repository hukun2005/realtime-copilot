"""VAD Provider 抽象：统一 Segmenter 的接入方式，便于替换算法。

``EnergyVadProvider`` 是本仓库默认实现；``WebRtcVadProvider`` 依赖可选的
``webrtcvad`` 包，未安装时构造会抛 :class:`VadUnavailable`，不影响默认路径。
"""

from __future__ import annotations

from typing import Protocol

from .vad import Segmenter, VadEvent, VadFrame


class VadUnavailable(RuntimeError):
    """可选的 VAD 后端不可用。"""


class VadProvider(Protocol):
    """VAD 后端接口；实现不得改变 ``SpeechSegment`` 结构。"""

    def push(self, frame: VadFrame) -> list[VadEvent]:
        """处理一帧。"""

    def flush(self, source: str, ts_ns: int | None = None) -> list[VadEvent]:
        """结束当前片段。"""


class EnergyVadProvider:
    """基于 ``Segmenter`` 的能量 VAD 默认实现。"""

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
        self.segmenter = Segmenter(
            threshold_dbfs=threshold_dbfs,
            start_ms=start_ms,
            end_silence_ms=end_silence_ms,
            pre_roll_ms=pre_roll_ms,
            max_segment_ms=max_segment_ms,
            frame_ms=frame_ms,
        )

    def push(self, frame: VadFrame) -> list[VadEvent]:
        return self.segmenter.push(frame)

    def flush(self, source: str, ts_ns: int | None = None) -> list[VadEvent]:
        return self.segmenter.flush(source, ts_ns)


class WebRtcVadProvider:
    """WebRTC VAD 包装（可选依赖）。

    以 20ms / 16kHz / 单声道 PCM 为输入；采样率或声道不符时抛 ``ValueError``。
    """

    def __init__(self, *, mode: int = 2) -> None:
        try:
            import webrtcvad  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise VadUnavailable("webrtcvad is not installed") from exc

        if not 0 <= mode <= 3:
            raise ValueError("webrtcvad mode must be 0..3")
        self._vad = webrtcvad.Vad(mode)
        self._segmenter = Segmenter(start_ms=120, end_silence_ms=400, pre_roll_ms=2000)

    @staticmethod
    def available() -> bool:
        try:
            import webrtcvad  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            return False
        return True

    def push(self, frame: VadFrame) -> list[VadEvent]:
        if frame.sample_rate != 16000 or frame.channels != 1:
            raise ValueError("webrtcvad requires 16kHz mono PCM")
        if len(frame.pcm16) != 640:
            raise ValueError("webrtcvad requires 20ms frames (640 bytes)")
        speech = self._vad.is_speech(frame.pcm16, 16000)
        peak = frame.peak_dbfs if speech else -120.0
        adapted = VadFrame(
            source=frame.source,
            sequence=frame.sequence,
            capture_ts_ns=frame.capture_ts_ns,
            pcm16=frame.pcm16,
            sample_rate=frame.sample_rate,
            channels=frame.channels,
            frame_duration_ms=frame.frame_duration_ms,
            peak_dbfs=peak,
            rms_dbfs=frame.rms_dbfs if speech else -120.0,
        )
        return self._segmenter.push(adapted)

    def flush(self, source: str, ts_ns: int | None = None) -> list[VadEvent]:
        return self._segmenter.flush(source, ts_ns)
