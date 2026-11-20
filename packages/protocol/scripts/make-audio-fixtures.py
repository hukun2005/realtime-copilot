#!/usr/bin/env python3
"""生成音频帧协议的跨语言 fixtures。

样本是确定性的：同一脚本在任何机器上生成逐字节相同的结果。
运行：uv run python packages/protocol/scripts/make-audio-fixtures.py
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

HEADER = struct.Struct("<BBH I Q I H H I")
OUTPUT = Path(__file__).resolve().parents[1] / "fixtures" / "audio"

PAYLOAD = bytes((index * 7 + 13) % 256 for index in range(640))


def build(
    *,
    version: int = 1,
    source: int = 0,
    duration_ms: int = 20,
    sequence: int = 1,
    capture_ts_ns: int = 1_000_000_000,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_format: int = 1,
    payload: bytes = PAYLOAD,
    declared_length: int | None = None,
) -> bytes:
    """按 v1 布局拼一帧。"""
    header = HEADER.pack(
        version,
        source,
        duration_ms,
        sequence,
        capture_ts_ns,
        sample_rate,
        channels,
        sample_format,
        len(payload) if declared_length is None else declared_length,
    )
    return header + payload


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)

    valid = build()
    (OUTPUT / "valid-frame.bin").write_bytes(valid)
    (OUTPUT / "valid-frame.meta.json").write_text(
        json.dumps(
            {
                "protocolVersion": 1,
                "source": "loopback",
                "frameDurationMs": 20,
                "sequence": 1,
                "captureTsNs": 1_000_000_000,
                "sampleRate": 16000,
                "channels": 1,
                "sampleFormat": 1,
                "payloadLength": 640,
                "totalBytes": len(valid),
                "payloadSha256Prefix": "ramp",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (OUTPUT / "invalid-version.bin").write_bytes(build(version=2))
    (OUTPUT / "invalid-rate.bin").write_bytes(build(sample_rate=44_100))
    (OUTPUT / "invalid-channels.bin").write_bytes(build(channels=2))
    (OUTPUT / "invalid-format.bin").write_bytes(build(sample_format=2))
    (OUTPUT / "truncated-header.bin").write_bytes(build()[:20])
    (OUTPUT / "payload-mismatch.bin").write_bytes(
        build(payload=PAYLOAD[:100], declared_length=640)
    )

    print(f"wrote {len(list(OUTPUT.glob('*.bin')))} fixtures to {OUTPUT}")


if __name__ == "__main__":
    main()
