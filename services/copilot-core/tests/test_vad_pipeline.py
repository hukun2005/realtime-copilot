"""P3.1：VAD 运行时接线、片段 PCM 与有界 ASR 队列。"""

from __future__ import annotations

import asyncio
import struct
import wave
from pathlib import Path

import pytest
from copilot_core.asr.pipeline import AsrPipeline
from copilot_core.asr.vad import Segmenter, SpeechSegment, VadEventKind, VadFrame
from copilot_core.audio.level import peak_dbfs, rms_dbfs

FIXTURE = Path(__file__).parent / "fixtures" / "vad-sample.wav"
FRAME_SAMPLES = 320  # 20ms @ 16k


def pcm(amplitude: int) -> bytes:
    return struct.pack("<320h", *([amplitude, -amplitude] * 160))


def frame(
    source: str,
    sequence: int,
    ts_ms: int,
    amplitude: int,
) -> VadFrame:
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


def feed(
    segmenter: Segmenter,
    source: str,
    start_ms: int,
    count: int,
    amplitude: int,
) -> list:
    events = []
    for index in range(count):
        events.extend(
            segmenter.push(frame(source, index, start_ms + index * 20, amplitude))
        )
    return events


def segments_in(events: list) -> list[SpeechSegment]:
    return [event.segment for event in events if event.kind is VadEventKind.SPEECH_ENDED]


def test_pure_silence_emits_no_segment() -> None:
    segmenter = Segmenter(start_ms=40, end_silence_ms=60)
    feed(segmenter, "loopback", 0, 50, 0)
    assert segmenter.flush("loopback") == []


def test_continuous_speech_segment_contains_pcm() -> None:
    segmenter = Segmenter(start_ms=40, end_silence_ms=60)
    feed(segmenter, "loopback", 0, 20, 8000)
    segments = segments_in(segmenter.flush("loopback"))
    assert len(segments) == 1
    segment = segments[0]
    assert segment.source == "loopback"
    assert segment.frames > 0
    assert len(segment.pcm16) == segment.frames * 640
    assert segment.start_ts_ns < segment.end_ts_ns


def test_short_noise_does_not_start_segment() -> None:
    segmenter = Segmenter(start_ms=120, end_silence_ms=60)
    feed(segmenter, "loopback", 0, 3, 8000)
    assert segmenter.flush("loopback") == []


def test_pre_roll_and_start_frames_are_included() -> None:
    segmenter = Segmenter(start_ms=40, end_silence_ms=60, pre_roll_ms=200)
    feed(segmenter, "loopback", 0, 10, 0)      # 200ms silence
    events = feed(segmenter, "loopback", 200, 10, 8000)
    segment = segments_in(segmenter.flush("loopback", 400))[0]

    assert segment.frames > 10
    assert segment.start_ts_ns < 200_000_000
    assert any(event.kind is VadEventKind.SPEECH_STARTED for event in events)


def test_end_silence_is_not_counted_as_speech() -> None:
    segmenter = Segmenter(start_ms=40, end_silence_ms=100)
    feed(segmenter, "loopback", 0, 10, 8000)
    events = feed(segmenter, "loopback", 200, 10, 0)
    segment = segments_in(events)[0]

    assert segment.end_silence_ms >= 100
    assert segment.end_ts_ns == 180_000_000
    assert len(segment.pcm16) == segment.frames * 640


def test_pause_splits_into_two_segments() -> None:
    segmenter = Segmenter(start_ms=40, end_silence_ms=60)
    feed(segmenter, "loopback", 0, 10, 8000)
    first = segments_in(feed(segmenter, "loopback", 200, 10, 0))
    feed(segmenter, "loopback", 400, 10, 8000)
    second = segments_in(segmenter.flush("loopback", 600))

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].segment_id != second[0].segment_id


def test_long_speech_is_split_by_max_segment() -> None:
    segmenter = Segmenter(start_ms=20, end_silence_ms=100, max_segment_ms=100)
    events = feed(segmenter, "loopback", 0, 30, 8000)
    events.extend(segmenter.flush("loopback", 600))
    segments = segments_in(events)

    assert len(segments) >= 2
    for segment in segments:
        assert segment.end_ts_ns - segment.start_ts_ns <= 200_000_000


def test_loopback_and_mic_are_independent() -> None:
    segmenter = Segmenter(start_ms=40, end_silence_ms=60)
    feed(segmenter, "loopback", 0, 10, 8000)
    assert segmenter.is_active("loopback")
    assert not segmenter.is_active("mic")

    feed(segmenter, "mic", 0, 10, 4000)
    assert segmenter.is_active("mic")
    segments = segments_in(segmenter.flush("mic", 400))
    assert segments and segments[0].source == "mic"
    assert segmenter.is_active("loopback")


def test_timestamp_regression_is_rejected() -> None:
    segmenter = Segmenter()
    segmenter.push(frame("loopback", 0, 100, 0))
    with pytest.raises(ValueError):
        segmenter.push(frame("loopback", 1, 50, 0))


def test_ring_buffer_is_bounded_and_counts_evictions() -> None:
    segmenter = Segmenter(pre_roll_ms=200, frame_ms=20, start_ms=40)
    for index in range(100):
        segmenter.push(frame("loopback", index, index * 20, 0))

    assert segmenter.buffered_frames("loopback") <= 14
    assert segmenter.ring_evictions("loopback") > 0


async def _drain(pipeline: AsrPipeline, source: str, attempts: int = 50) -> SpeechSegment | None:
    for _ in range(attempts):
        segment = pipeline.next_segment(source)
        if segment is not None:
            return segment
        await asyncio.sleep(0.01)
    return None


async def test_pipeline_is_bounded_and_drops_segments_when_full() -> None:
    pipeline = AsrPipeline(queue_segments=1, queue_frames=100)
    pipeline.start()
    try:
        for index in range(10):
            pipeline.ingest(frame("loopback", index, index * 20, 8000))
        await asyncio.sleep(0.05)
        # 结束一段后再产生一段：第二个片段因队列满被丢弃。
        for index in range(10, 40):
            pipeline.ingest(frame("loopback", index, index * 20, 0))
        for index in range(40, 70):
            pipeline.ingest(frame("loopback", index, index * 20, 8000))
        await asyncio.sleep(0.1)

        # stop 时 flush 第二个片段；ASR 队列只有 1 个位置，因此必然丢弃。
        await pipeline.stop()
        assert pipeline.stats.segments_dropped >= 1
        assert pipeline.snapshot()["segmentQueueDepth"] <= 1
    finally:
        await pipeline.stop()


async def test_stop_flushes_active_segment() -> None:
    pipeline = AsrPipeline()
    pipeline.start()
    for index in range(10):
        pipeline.ingest(frame("loopback", index, index * 20, 8000))
    await asyncio.sleep(0.05)

    await pipeline.stop()
    segment = pipeline.next_segment("loopback")
    assert segment is not None
    assert segment.pcm16


async def test_vad_error_does_not_stop_pipeline() -> None:
    class Broken(Segmenter):
        def __init__(self) -> None:
            super().__init__(start_ms=20)
            self.calls = 0

        def push(self, audio: VadFrame):  # type: ignore[override]
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return super().push(audio)

    pipeline = AsrPipeline(segmenter=Broken())
    pipeline.start()
    try:
        for index in range(20):
            pipeline.ingest(frame("loopback", index, index * 20, 8000))
        await asyncio.sleep(0.1)
        assert pipeline.stats.vad_errors >= 1
        assert pipeline.stats.frames_accepted >= 10
    finally:
        await pipeline.stop()


def test_real_pcm_fixture_produces_matching_segment() -> None:
    with wave.open(str(FIXTURE), "rb") as handle:
        assert handle.getframerate() == 16000
        raw = handle.readframes(handle.getnframes())

    segmenter = Segmenter(start_ms=40, end_silence_ms=60, pre_roll_ms=200)
    frame_bytes = FRAME_SAMPLES * 2
    events = []
    for index in range(len(raw) // frame_bytes):
        chunk = raw[index * frame_bytes : (index + 1) * frame_bytes]
        events.extend(
            segmenter.push(
                VadFrame(
                    source="loopback",
                    sequence=index,
                    capture_ts_ns=index * 20_000_000,
                    pcm16=chunk,
                    sample_rate=16000,
                    channels=1,
                    frame_duration_ms=20,
                    peak_dbfs=peak_dbfs(chunk),
                    rms_dbfs=rms_dbfs(chunk),
                )
            )
        )
    events.extend(segmenter.flush("loopback", len(raw) // 2 * 20))

    segments = segments_in(events)
    assert segments
    segment = segments[0]
    assert segment.pcm16 in raw or raw.find(segment.pcm16) >= 0
    assert segment.peak_dbfs > -50
    assert segment.frames * 640 == len(segment.pcm16)
