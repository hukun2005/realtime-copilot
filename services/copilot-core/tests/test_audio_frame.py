"""音频帧 v1 的结构解析：fixtures、字段校验与编码往返。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from copilot_core.audio import (
    FRAME_BYTES,
    HEADER_BYTES,
    PAYLOAD_BYTES,
    FrameError,
    build_frame,
    encode_frame,
    parse_frame,
)

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "protocol" / "fixtures" / "audio"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_valid_fixture_matches_metadata() -> None:
    meta = json.loads((FIXTURES / "valid-frame.meta.json").read_text(encoding="utf-8"))
    frame = parse_frame(fixture("valid-frame.bin"))

    assert frame.protocol_version == meta["protocolVersion"]
    assert frame.source == meta["source"]
    assert frame.frame_duration_ms == meta["frameDurationMs"]
    assert frame.sequence == meta["sequence"]
    assert frame.capture_ts_ns == meta["captureTsNs"]
    assert frame.sample_rate == meta["sampleRate"]
    assert frame.channels == meta["channels"]
    assert frame.sample_format == meta["sampleFormat"]
    assert len(frame.payload) == meta["payloadLength"]
    assert meta["payloadLength"] == PAYLOAD_BYTES
    assert meta["totalBytes"] == FRAME_BYTES


def test_valid_fixture_has_expected_payload_pattern() -> None:
    frame = parse_frame(fixture("valid-frame.bin"))
    assert frame.payload[:4] == bytes([13, 20, 27, 34])
    assert frame.payload[-1] == ((639 * 7) + 13) % 256


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("invalid-version.bin", "unsupported_version"),
        ("invalid-rate.bin", "invalid_sample_rate"),
        ("invalid-channels.bin", "invalid_channels"),
        ("invalid-format.bin", "invalid_sample_format"),
        ("truncated-header.bin", "truncated_header"),
        ("payload-mismatch.bin", "payload_length_mismatch"),
    ],
)
def test_invalid_fixtures_report_expected_code(name: str, code: str) -> None:
    with pytest.raises(FrameError) as excinfo:
        parse_frame(fixture(name))
    assert excinfo.value.code == code


def test_encode_roundtrip_is_byte_identical() -> None:
    frame = parse_frame(fixture("valid-frame.bin"))
    assert encode_frame(frame) == fixture("valid-frame.bin")


def test_build_frame_defaults_are_valid() -> None:
    frame = build_frame(sequence=7, capture_ts_ns=123)
    parsed = parse_frame(encode_frame(frame))
    assert parsed == frame
    assert len(encode_frame(frame)) == HEADER_BYTES + PAYLOAD_BYTES == FRAME_BYTES


def test_invalid_source_is_rejected() -> None:
    data = bytearray(fixture("valid-frame.bin"))
    data[1] = 9
    with pytest.raises(FrameError) as excinfo:
        parse_frame(bytes(data))
    assert excinfo.value.code == "invalid_source"


def test_wrong_duration_is_rejected() -> None:
    data = bytearray(fixture("valid-frame.bin"))
    data[2:4] = (40).to_bytes(2, "little")
    with pytest.raises(FrameError) as excinfo:
        parse_frame(bytes(data))
    assert excinfo.value.code == "invalid_frame_duration"


def test_payload_must_be_exactly_640_bytes() -> None:
    header = fixture("valid-frame.bin")[:HEADER_BYTES]
    with pytest.raises(FrameError) as excinfo:
        parse_frame(header + bytes(320))
    assert excinfo.value.code in {"payload_length_mismatch", "invalid_payload_size"}
