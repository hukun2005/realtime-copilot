"""音频二进制帧 v1：解析与编码。

布局见 ``packages/protocol/audio-frame.v1.md``：28 字节小端头 + 640 字节 PCM16LE。

解析器只做结构校验，不解释 PCM 语义；任何不合法输入都抛 :class:`FrameError`，
由会话层决定丢弃与计数，绝不让异常冒泡到 ASGI 层。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

PROTOCOL_VERSION: Final = 1
HEADER_BYTES: Final = 28
PAYLOAD_BYTES: Final = 640
FRAME_BYTES: Final = HEADER_BYTES + PAYLOAD_BYTES
SAMPLE_RATE: Final = 16000
CHANNELS: Final = 1
SAMPLE_FORMAT_PCM16: Final = 1
FRAME_DURATION_MS: Final = 20

SOURCE_LOOPBACK: Final = 0
SOURCE_MIC: Final = 1
SOURCE_NAMES: Final[dict[int, str]] = {SOURCE_LOOPBACK: "loopback", SOURCE_MIC: "mic"}
SOURCE_CODES: Final[dict[str, int]] = {name: code for code, name in SOURCE_NAMES.items()}

#: ``<BBH I Q I H H I`` —— 小端，含 1 字节对齐填充前的自然排布。
_HEADER = struct.Struct("<BBHIQIHHI")


class FrameError(Exception):
    """一帧不符合协议。

    Args:
        code: 稳定的机器可读错误码（见协议文档 §7）。
        detail: 诊断信息；不得包含 PCM 内容。
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """一帧经过校验的音频。

    ``payload`` 只存在于内存，不落盘、不进日志。
    """

    protocol_version: int
    source: str
    frame_duration_ms: int
    sequence: int
    capture_ts_ns: int
    sample_rate: int
    channels: int
    sample_format: int
    payload: bytes


def parse_frame(data: bytes) -> AudioFrame:
    """解析并校验一帧。

    Args:
        data: 一条 WebSocket 二进制消息的完整内容。

    Returns:
        校验通过的帧。

    Raises:
        FrameError: 头部截断、版本不支持或任一字段不合法。
    """
    if len(data) < HEADER_BYTES:
        raise FrameError("truncated_header", f"{len(data)} < {HEADER_BYTES}")

    (
        version,
        source_code,
        duration_ms,
        sequence,
        capture_ts_ns,
        sample_rate,
        channels,
        sample_format,
        payload_length,
    ) = _HEADER.unpack_from(data, 0)

    if version != PROTOCOL_VERSION:
        raise FrameError("unsupported_version", f"v{version}")

    source = SOURCE_NAMES.get(source_code)
    if source is None:
        raise FrameError("invalid_source", str(source_code))

    if duration_ms != FRAME_DURATION_MS:
        raise FrameError("invalid_frame_duration", str(duration_ms))

    if sample_rate != SAMPLE_RATE:
        raise FrameError("invalid_sample_rate", str(sample_rate))

    if channels != CHANNELS:
        raise FrameError("invalid_channels", str(channels))

    if sample_format != SAMPLE_FORMAT_PCM16:
        raise FrameError("invalid_sample_format", str(sample_format))

    actual_payload = len(data) - HEADER_BYTES
    if payload_length != actual_payload:
        raise FrameError("payload_length_mismatch", f"declared {payload_length}, actual {actual_payload}")

    if payload_length != PAYLOAD_BYTES:
        raise FrameError("invalid_payload_size", str(payload_length))

    return AudioFrame(
        protocol_version=version,
        source=source,
        frame_duration_ms=duration_ms,
        sequence=sequence,
        capture_ts_ns=capture_ts_ns,
        sample_rate=sample_rate,
        channels=channels,
        sample_format=sample_format,
        payload=data[HEADER_BYTES:],
    )


def encode_frame(frame: AudioFrame) -> bytes:
    """把一帧编码回字节，供测试与模拟客户端使用。

    Args:
        frame: 要编码的帧。

    Returns:
        668 字节的消息内容。
    """
    header = _HEADER.pack(
        frame.protocol_version,
        SOURCE_CODES[frame.source],
        frame.frame_duration_ms,
        frame.sequence,
        frame.capture_ts_ns,
        frame.sample_rate,
        frame.channels,
        frame.sample_format,
        len(frame.payload),
    )
    return header + frame.payload


def build_frame(
    *,
    sequence: int,
    capture_ts_ns: int,
    source: str = "loopback",
    payload: bytes | None = None,
) -> AudioFrame:
    """构造一帧默认格式的音频，便于测试与模拟。"""
    return AudioFrame(
        protocol_version=PROTOCOL_VERSION,
        source=source,
        frame_duration_ms=FRAME_DURATION_MS,
        sequence=sequence,
        capture_ts_ns=capture_ts_ns,
        sample_rate=SAMPLE_RATE,
        channels=CHANNELS,
        sample_format=SAMPLE_FORMAT_PCM16,
        payload=payload if payload is not None else bytes(PAYLOAD_BYTES),
    )
