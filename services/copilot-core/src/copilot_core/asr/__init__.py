"""ASR 接入的准备模块：协议约束、状态与 VAD。

当前阶段只做接口与本地 VAD，不接任何识别服务。
"""

from .state import AsrState, AsrStatusTracker
from .stream import AsrStreamError, AsrStreamTracker, AsrViolation
from .vad import EnergyVad, Segmenter, SpeechSegment, VadEvent, VadEventKind

__all__ = [
    "AsrState",
    "AsrStatusTracker",
    "AsrStreamError",
    "AsrStreamTracker",
    "AsrViolation",
    "EnergyVad",
    "Segmenter",
    "SpeechSegment",
    "VadEvent",
    "VadEventKind",
]
