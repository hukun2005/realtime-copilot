"""音频二进制帧接收：解析、会话状态与统计。"""

from .frame import (
    FRAME_BYTES,
    FRAME_DURATION_MS,
    HEADER_BYTES,
    PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    SAMPLE_RATE,
    AudioFrame,
    FrameError,
    build_frame,
    encode_frame,
    parse_frame,
)
from .hub import AudioHub
from .session import AudioSession, FrameOutcome
from .sink import AudioFrameSink, InMemoryAudioSink

__all__ = [
    "FRAME_BYTES",
    "FRAME_DURATION_MS",
    "HEADER_BYTES",
    "PAYLOAD_BYTES",
    "PROTOCOL_VERSION",
    "SAMPLE_RATE",
    "AudioFrame",
    "AudioFrameSink",
    "AudioHub",
    "AudioSession",
    "FrameError",
    "FrameOutcome",
    "InMemoryAudioSink",
    "build_frame",
    "encode_frame",
    "parse_frame",
]
