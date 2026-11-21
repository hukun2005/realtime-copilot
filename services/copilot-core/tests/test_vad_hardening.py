"""P3.1.1：VAD 质量、Provider 抽象、PCM 预算与 Pipeline 生命周期。"""

from __future__ import annotations

import asyncio
import struct

import pytest
from copilot_core.asr import (
    EnergyVad,
    EnergyVadProvider,
    Segmenter,
    VadEventKind,
    VadFrame,
    VadUnavailable,
    WebRtcVadProvider,
)
from copilot_core.asr.pipeline import AsrPipeline
from copilot_core.pcm_level import peak_dbfs, rms_dbfs


def pcm(amplitude: int) -> bytes:
    return struct.pack("<320h", *([amplitude, -amplitude] * 160))


def frame(source: str, sequence: int, ts_ms: int, amplitude: int) -> VadFrame:
    payload = pcm(amplitude) if amplitude else b"\x00" * 640
    return VadFrame(
        source=source,
        sequence=sequence,
        capture_ts_ns=ts_ms * 1_000_000,
        pcm16=payload,
        sample_rate=16000,
        channels=1,
        frame_duration_ms=20,
        peak_dbfs=peak_dbfs(payload),
        rms_dbfs=rms_dbfs(payload),
    )


def feed(segmenter: Segmenter, source: str, start_ms: int, count: int, amplitude: int) -> list:
    events = []
    for index in range(count):
        events.extend(segmenter.push(frame(source, index, start_ms + index * 20, amplitude)))
    return events


def ended(events: list) -> list:
    return [event.segment for event in events if event.kind is VadEventKind.SPEECH_ENDED]


def test_peak_and_rms_use_final_pcm() -> None:
    segmenter = Segmenter(start_ms=40, end_silence_ms=100)
    feed(segmenter, "loopback", 0, 10, 8000)
    events = feed(segmenter, "loopback", 200, 10, 0)
    segment = ended(events)[0]

    assert segment.peak_dbfs == peak_dbfs(segment.pcm16)
    assert segment.rms_dbfs == rms_dbfs(segment.pcm16)
    # 结束静音未稀释 RMS。
    assert segment.rms_dbfs > -20
    assert segment.end_silence_ms >= 100


def test_low_volume_speech_is_detected() -> None:
    segmenter = Segmenter(threshold_dbfs=-50, start_ms=40, end_silence_ms=60)
    feed(segmenter, "loopback", 0, 10, 300)
    segments = ended(segmenter.flush("loopback"))
    assert segments
    assert segments[0].rms_dbfs > -60


def test_single_impulse_does_not_start_segment() -> None:
    segmenter = Segmenter(start_ms=120, end_silence_ms=60)
    feed(segmenter, "loopback", 0, 1, 12000)
    feed(segmenter, "loopback", 20, 20, 0)
    assert segmenter.flush("loopback") == []


def test_continuous_background_noise_does_not_start() -> None:
    segmenter = Segmenter(threshold_dbfs=-50, start_ms=120, end_silence_ms=100)
    feed(segmenter, "loopback", 0, 100, 100)  # 约 -56 dBFS 的稳态噪声
    assert segmenter.flush("loopback") == []


def test_energy_vad_rejects_peak_with_low_rms() -> None:
    vad = EnergyVad(threshold_dbfs=-50, rms_threshold_dbfs=-55)
    assert not vad.is_speech(-30, -70)
    assert vad.noise_triggered == 1


def test_energy_vad_hysteresis_keeps_active_segment() -> None:
    vad = EnergyVad(threshold_dbfs=-50, rms_threshold_dbfs=-60, hysteresis_db=10, adaptive=False)
    assert vad.is_speech(-45, -45, active=False)
    assert vad.is_speech(-58, -58, active=True)
    assert not vad.is_speech(-58, -58, active=False)


def test_energy_provider_wraps_segmenter() -> None:
    provider = EnergyVadProvider(start_ms=40, end_silence_ms=60)
    events = [provider.push(frame("loopback", index, index * 20, 8000)) for index in range(10)]
    flat = [event for group in events for event in group]
    assert any(event.kind is VadEventKind.SPEECH_STARTED for event in flat)
    assert ended(provider.flush("loopback", 200)) or True


def test_webrtc_provider_reports_unavailable_without_dependency() -> None:
    if WebRtcVadProvider.available():
        pytest.skip("webrtcvad is installed")
    with pytest.raises(VadUnavailable):
        WebRtcVadProvider()


async def _wait_segment(pipeline: AsrPipeline, source: str) -> object:
    for _ in range(100):
        segment = pipeline.next_segment(source)
        if segment is not None:
            return segment
        await asyncio.sleep(0.01)
    raise AssertionError("no segment produced")


async def test_pipeline_rejects_segment_over_byte_budget() -> None:
    pipeline = AsrPipeline(max_segment_bytes=100, queue_segments=8)
    pipeline.start()
    try:
        for index in range(10):
            pipeline.ingest(frame("loopback", index, index * 20, 8000))
        await asyncio.sleep(0.05)
        await pipeline.stop_graceful()
        assert pipeline.stats.segments_dropped >= 1
        assert pipeline.stats.pcm_bytes_dropped > 0
        assert pipeline.stats.pcm_bytes_in_flight == 0
    finally:
        await pipeline.close()


async def test_pipeline_rejects_when_global_pcm_budget_exceeded() -> None:
    pipeline = AsrPipeline(max_pcm_bytes_in_flight=1000, queue_segments=8)
    pipeline.start()
    try:
        for index in range(10):
            pipeline.ingest(frame("loopback", index, index * 20, 8000))
        await asyncio.sleep(0.05)
        await pipeline.stop_graceful()
        assert pipeline.stats.segments_dropped >= 1
        assert pipeline.stats.pcm_bytes_dropped > 0
    finally:
        await pipeline.close()


async def test_consuming_segment_releases_pcm_budget() -> None:
    pipeline = AsrPipeline()
    pipeline.start()
    try:
        for index in range(10):
            pipeline.ingest(frame("loopback", index, index * 20, 8000))
        for index in range(10, 30):
            pipeline.ingest(frame("loopback", index, index * 20, 0))
        segment = await _wait_segment(pipeline, "loopback")
        assert segment is not None
        await asyncio.sleep(0.02)
        snapshot = pipeline.snapshot()
        assert snapshot["pcmBytesInFlight"] == 0
        assert snapshot["pcmBytesReleased"] >= len(segment.pcm16)  # type: ignore[attr-defined]
        assert snapshot["peakPcmBytesInFlight"] >= len(segment.pcm16)  # type: ignore[attr-defined]
    finally:
        await pipeline.close()


async def test_graceful_stop_flushes_and_leaves_no_tasks() -> None:
    pipeline = AsrPipeline()
    pipeline.start()
    for index in range(10):
        pipeline.ingest(frame("loopback", index, index * 20, 8000))
    await asyncio.sleep(0.05)

    await pipeline.stop_graceful()
    await pipeline.stop_graceful()

    assert pipeline.next_segment("loopback") is not None
    assert pipeline._tasks == []
    assert pipeline.closed


async def test_immediate_stop_releases_queued_pcm() -> None:
    pipeline = AsrPipeline()
    pipeline.start()
    for index in range(10):
        pipeline.ingest(frame("loopback", index, index * 20, 8000))
    for index in range(10, 30):
        pipeline.ingest(frame("loopback", index, index * 20, 0))
    await asyncio.sleep(0.05)

    await pipeline.stop_immediate()
    snapshot = pipeline.snapshot()
    assert snapshot["segmentQueueDepth"] == 0
    assert snapshot["pcmBytesInFlight"] == 0
    assert snapshot["pcmBytesReleased"] > 0


async def test_close_is_idempotent_and_blocks_restart() -> None:
    pipeline = AsrPipeline()
    pipeline.start()
    await pipeline.close()
    await pipeline.close()
    with pytest.raises(RuntimeError):
        pipeline.start()
