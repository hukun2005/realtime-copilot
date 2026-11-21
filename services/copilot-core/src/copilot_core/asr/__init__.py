"""ASR 接入的准备模块：协议约束、状态、VAD 与 VAD Provider。"""

from .state import AsrState, AsrStatusTracker
from .stream import AsrStreamError, AsrStreamTracker, AsrViolation
from .vad import EnergyVad, Segmenter, SpeechSegment, VadEvent, VadEventKind, VadFrame
from .vad_provider import EnergyVadProvider, VadProvider, VadUnavailable, WebRtcVadProvider

__all__ = [
    "AsrState",
    "AsrStatusTracker",
    "AsrStreamError",
    "AsrStreamTracker",
    "AsrViolation",
    "EnergyVad",
    "EnergyVadProvider",
    "Segmenter",
    "SpeechSegment",
    "VadEvent",
    "VadEventKind",
    "VadFrame",
    "VadProvider",
    "VadUnavailable",
    "WebRtcVadProvider",
]
