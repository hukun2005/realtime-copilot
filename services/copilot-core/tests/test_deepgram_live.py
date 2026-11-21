"""Deepgram 真实 API 测试；缺少 DEEPGRAM_API_KEY 时整体跳过。"""

from __future__ import annotations

import os

import pytest
from copilot_core.asr import AsrProviderConfig, DeepgramProvider, SpeechSegment
from copilot_core.asr.vad import VadFrame

pytestmark = pytest.mark.skipif(
    not os.environ.get("DEEPGRAM_API_KEY"),
    reason="DEEPGRAM_API_KEY is not set; real API validation is SKIPPED",
)


def pcm_tone(seconds: float = 2.0) -> bytes:
    import math
    import struct

    rate = 16000
    return b"".join(
        struct.pack("<h", int(9000 * math.sin(2 * math.pi * 440 * index / rate)))
        for index in range(int(rate * seconds))
    )


async def test_live_deepgram_returns_partial_or_final() -> None:
    payload = pcm_tone()
    segment = SpeechSegment(
        segment_id="seg_live",
        source="loopback",
        start_ts_ns=0,
        end_ts_ns=len(payload) // 2 * 1_000_000 // 16000,
        frames=len(payload) // 640,
        pcm16=payload,
        sample_rate=16000,
        channels=1,
        peak_dbfs=-12,
        rms_dbfs=-15,
    )
    config = AsrProviderConfig(name="deepgram", api_key_env="DEEPGRAM_API_KEY", model="nova-3")
    provider = DeepgramProvider(config)
    await provider.connect("live-ticket")
    try:
        results = [result async for result in provider.transcribe(segment)]
    finally:
        await provider.close()

    assert results
    assert any(result.final for result in results)


def test_vad_frame_contract_is_unchanged() -> None:
    frame = VadFrame(
        source="loopback",
        sequence=0,
        capture_ts_ns=0,
        pcm16=b"\x00" * 640,
        sample_rate=16000,
        channels=1,
        frame_duration_ms=20,
        peak_dbfs=-120,
        rms_dbfs=-120,
    )
    assert frame.sample_rate == 16000
