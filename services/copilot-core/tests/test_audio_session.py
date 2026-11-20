"""音频会话：序号连续性、时间戳、速率限制与断开阈值。"""

from __future__ import annotations

from copilot_core.audio import (
    AudioSession,
    InMemoryAudioSink,
    build_frame,
    encode_frame,
)
from copilot_core.audio.stats import AudioCounters


class FakeClock:
    """可推进的单调时钟。"""

    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds

    def advance_ms(self, milliseconds: float) -> None:
        self.advance(milliseconds / 1000)


def make_session(*, clock: FakeClock | None = None, **overrides: int) -> tuple[AudioSession, AudioCounters, InMemoryAudioSink]:
    counters = AudioCounters()
    sink = InMemoryAudioSink()
    options = {
        "max_frame_bytes": 4096,
        "max_frames_per_second": 200,
        "burst_frames": 250,
    }
    options.update(overrides)
    session = AudioSession(
        counters=counters,
        sink=sink,
        now=clock or FakeClock(),
        **options,
    )
    return session, counters, sink


def frame_bytes(sequence: int, ts_ns: int, *, source: str = "loopback") -> bytes:
    return encode_frame(build_frame(sequence=sequence, capture_ts_ns=ts_ns, source=source))


def test_continuous_sequence_is_accepted() -> None:
    session, counters, sink = make_session()
    for index in range(1, 11):
        outcome = session.handle(frame_bytes(index, 1_000_000_000 + index * 20_000_000))
        assert outcome.accepted

    assert counters.frames == 10
    assert counters.gaps == 0
    assert counters.duplicates == 0
    assert sink.frames == 10


def test_gap_is_counted_and_current_frame_accepted() -> None:
    session, counters, _ = make_session()
    session.handle(frame_bytes(1, 1_000_000_000))
    outcome = session.handle(frame_bytes(5, 1_000_080_000))

    assert outcome.accepted
    assert counters.gaps == 3
    assert counters.gap_events == 1
    assert counters.frames == 2


def test_duplicate_sequence_is_dropped() -> None:
    session, counters, sink = make_session()
    session.handle(frame_bytes(3, 1_000_000_000))
    outcome = session.handle(frame_bytes(3, 1_000_020_000))

    assert not outcome.accepted
    assert outcome.code == "duplicate_sequence"
    assert counters.duplicates == 1
    assert sink.frames == 1


def test_sequence_wraparound_is_continuous() -> None:
    session, counters, _ = make_session()
    session.handle(frame_bytes(0xFFFFFFFF, 1_000_000_000))
    outcome = session.handle(frame_bytes(0, 1_000_020_000))

    assert outcome.accepted
    assert counters.gaps == 0
    assert counters.duplicates == 0


def test_sequences_are_tracked_per_source() -> None:
    session, counters, _ = make_session()
    session.handle(frame_bytes(10, 1_000_000_000, source="loopback"))
    session.handle(frame_bytes(1, 1_000_000_000, source="mic"))

    assert counters.gaps == 0
    assert counters.frames == 2


def test_zero_timestamp_is_invalid() -> None:
    session, counters, _ = make_session()
    outcome = session.handle(frame_bytes(1, 0))

    assert not outcome.accepted
    assert outcome.code == "timestamp_invalid"
    assert counters.invalid == 1
    assert counters.error_codes.get("timestamp_invalid") == 1


def test_large_timestamp_regression_is_dropped() -> None:
    session, counters, _ = make_session()
    session.handle(frame_bytes(1, 5_000_000_000))
    outcome = session.handle(frame_bytes(2, 2_000_000_000))

    assert not outcome.accepted
    assert outcome.code == "timestamp_regression"
    assert counters.timestamp_regressions == 1


def test_small_timestamp_jitter_is_tolerated() -> None:
    session, counters, _ = make_session()
    session.handle(frame_bytes(1, 5_000_000_000))
    outcome = session.handle(frame_bytes(2, 4_500_000_000))

    assert outcome.accepted
    assert counters.timestamp_regressions == 0


def test_invalid_frame_is_counted_with_code() -> None:
    session, counters, _ = make_session()
    bad = bytearray(frame_bytes(1, 1_000_000_000))
    bad[0] = 2

    outcome = session.handle(bytes(bad))
    assert not outcome.accepted
    assert outcome.code == "unsupported_version"
    assert counters.invalid == 1
    assert counters.error_codes == {"unsupported_version": 1}


def test_oversized_frame_is_rejected_before_parsing() -> None:
    session, counters, _ = make_session()
    outcome = session.handle(b"\x00" * 5000)

    assert not outcome.accepted
    assert outcome.code == "frame_too_large"
    assert counters.too_large == 1


def test_rate_limit_rejects_burst_and_requests_disconnect() -> None:
    clock = FakeClock()
    session, counters, _ = make_session(clock=clock, max_frames_per_second=10, burst_frames=12)

    accepted = 0
    for index in range(40):
        if session.handle(frame_bytes(index + 1, 1_000_000_000 + index * 1_000_000)).accepted:
            accepted += 1

    assert accepted == 12
    assert counters.rate_limited == 28
    assert session.should_disconnect() is True


def test_invalid_storm_requests_disconnect() -> None:
    clock = FakeClock()
    session, counters, _ = make_session(clock=clock, max_invalid_per_second=3, burst_frames=500)
    bad = bytearray(frame_bytes(1, 1_000_000_000))
    bad[0] = 2

    for _ in range(4):
        session.handle(bytes(bad))

    assert counters.invalid == 4
    assert session.should_disconnect() is True


def test_window_resets_after_one_second() -> None:
    clock = FakeClock()
    session, _, _ = make_session(clock=clock, max_invalid_per_second=2, burst_frames=500)
    bad = bytearray(frame_bytes(1, 1_000_000_000))
    bad[0] = 2

    session.handle(bytes(bad))
    session.handle(bytes(bad))
    assert session.should_disconnect() is False

    clock.advance(1.5)
    session.handle(frame_bytes(1, 1_000_000_000))
    assert session.should_disconnect() is False
