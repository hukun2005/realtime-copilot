"""按来源独立统计：loopback 与 mic 不能混在一起。"""

from __future__ import annotations

import struct

from copilot_core.audio import AudioSession, InMemoryAudioSink, build_frame, encode_frame
from copilot_core.audio.hub import AudioHub
from copilot_core.audio.source_stats import SourceStats
from copilot_core.audio.stats import AudioCounters
from copilot_core.config import AudioConfig


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def loud_payload(level: int = 8000) -> bytes:
    return struct.pack("<320h", *([level, -level] * 160))


def make_session(source_stats=None) -> AudioSession:
    return AudioSession(
        counters=AudioCounters(),
        sink=InMemoryAudioSink(),
        max_frame_bytes=4096,
        max_frames_per_second=200,
        burst_frames=250,
        now=FakeClock(),
        source_stats=source_stats,
    )


def feed(session: AudioSession, *, source: str, sequence: int, ts_ns: int, payload: bytes | None = None) -> None:
    frame = build_frame(sequence=sequence, capture_ts_ns=ts_ns, source=source, payload=payload)
    session.handle(encode_frame(frame))


def test_loopback_and_mic_stats_are_separate() -> None:
    session = make_session()
    feed(session, source="loopback", sequence=1, ts_ns=1_000_000_000, payload=loud_payload())
    feed(session, source="mic", sequence=1, ts_ns=1_000_000_000, payload=loud_payload(2000))

    stats = session.source_stats()
    assert stats["loopback"]["framesReceived"] == 1
    assert stats["mic"]["framesReceived"] == 1
    assert stats["loopback"]["peakDbfs"] != stats["mic"]["peakDbfs"]
    assert stats["loopback"]["currentSampleRate"] == 16000
    assert stats["loopback"]["currentChannels"] == 1


def test_silence_duration_accumulates_and_resets() -> None:
    session = make_session()
    for index in range(5):
        feed(session, source="loopback", sequence=index + 1, ts_ns=(index + 1) * 20_000_000, payload=b"\x00" * 640)

    stats = session.source_stats()["loopback"]
    assert stats["silenceDurationMs"] == 100

    feed(session, source="loopback", sequence=6, ts_ns=200_000_000, payload=loud_payload())
    assert session.source_stats()["loopback"]["silenceDurationMs"] == 0


def test_duplicate_is_counted_per_source() -> None:
    session = make_session()
    feed(session, source="mic", sequence=5, ts_ns=1_000_000_000)
    feed(session, source="mic", sequence=5, ts_ns=1_020_000_000)

    stats = session.source_stats()
    assert stats["mic"]["duplicates"] == 1
    assert stats["mic"]["framesDropped"] == 1
    assert "loopback" not in stats


def test_hub_exposes_sources_in_snapshot() -> None:
    hub = AudioHub(AudioConfig())
    session = hub.open_session()
    feed(session, source="loopback", sequence=1, ts_ns=1_000_000_000, payload=loud_payload())
    feed(session, source="mic", sequence=1, ts_ns=1_000_000_000, payload=loud_payload(500))

    snapshot = hub.snapshot()
    assert snapshot["sources"]["loopback"]["framesReceived"] == 1
    assert snapshot["sources"]["mic"]["framesReceived"] == 1
    assert snapshot["frames"] == 2


def test_audio_status_event_includes_sources() -> None:
    import logging

    from copilot_core.capture import CaptureController
    from copilot_core.config import CaptureConfig
    from copilot_core.tickets import TicketIssuer

    async def noop(_event) -> None:
        return None

    controller = CaptureController(
        config=CaptureConfig(),
        tickets=TicketIssuer(),
        logger=logging.getLogger("test.sources"),
        broadcast=noop,
        source_stats=lambda: {"loopback": SourceStats(frames_received=7).snapshot()},
    )

    event = controller._status_event()
    assert event.payload.sources is not None
    assert event.payload.sources.loopback is not None
    assert event.payload.sources.loopback.frames_received == 7