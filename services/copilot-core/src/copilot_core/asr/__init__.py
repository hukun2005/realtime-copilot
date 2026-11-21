"""ASR 接入模块：协议约束、状态、VAD、Provider 与运行时。"""

from .deepgram import DeepgramProvider
from .manager import AsrManager
from .provider import (
    AsrAuthError,
    AsrError,
    AsrProvider,
    AsrProviderConfig,
    AsrRegistry,
    AsrResult,
    AsrUnavailable,
    MockAsrProvider,
)
from .state import AsrState, AsrStatusTracker
from .stream import AsrStreamError, AsrStreamTracker, AsrViolation
from .vad import EnergyVad, Segmenter, SpeechSegment, VadEvent, VadEventKind, VadFrame
from .vad_provider import EnergyVadProvider, VadProvider, VadUnavailable, WebRtcVadProvider

AsrRegistry.register("deepgram", DeepgramProvider)

__all__ = [
    "AsrAuthError",
    "AsrError",
    "AsrManager",
    "AsrProvider",
    "AsrProviderConfig",
    "AsrRegistry",
    "AsrResult",
    "AsrState",
    "AsrStatusTracker",
    "AsrStreamError",
    "AsrStreamTracker",
    "AsrUnavailable",
    "AsrViolation",
    "DeepgramProvider",
    "EnergyVad",
    "EnergyVadProvider",
    "MockAsrProvider",
    "Segmenter",
    "SpeechSegment",
    "VadEvent",
    "VadEventKind",
    "VadFrame",
    "VadProvider",
    "VadUnavailable",
    "WebRtcVadProvider",
]
