"""P3：ASR 协议 fixture、流式约束、VAD 与状态独立性。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from copilot_core.asr import (
    AsrState,
    AsrStatusTracker,
    AsrStreamError,
    AsrStreamTracker,
    AsrViolation,
    EnergyVad,
    Segmenter,
    VadEventKind,
)
from copilot_core.capture import CaptureState, CaptureStateMachine

from copilot_protocol import parse_event

FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "protocol" / "fixtures"


@pytest.mark.parametrize("name", ["asr.status", "asr.partial", "asr.final", "asr.error"])
def test_asr_fixtures_parse(name: str) -> None:
    raw = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    event = parse_event(raw)
    assert event.type == name


def test_asr_partial_final_share_segment_and_revision_increases() -> None:
    tracker = AsrStreamTracker()
    tracker.accept_partial("seg_a", 0, 0, 100, "你")
    tracker.accept_partial("seg_a", 1, 0, 200, "你好")
    tracker.accept_final("seg_a", 2, 0, 260, "你好。")

    assert tracker.is_finalized("seg_a")
    assert tracker.last_revision("seg_a") == 2


def test_final_rejects_older_partial_and_duplicate_final() -> None:
    tracker = AsrStreamTracker()
    tracker.accept_final("seg_b", 3, 0, 100, "定稿")

    with pytest.raises(AsrStreamError) as stale:
        tracker.accept_partial("seg_b", 2, 0, 90, "旧 partial")
    assert stale.value.violation is AsrViolation.SEGMENT_FINALIZED

    with pytest.raises(AsrStreamError):
        tracker.accept_final("seg_b", 4, 0, 120, "再次定稿")


def test_stale_revision_is_rejected() -> None:
    tracker = AsrStreamTracker()
    tracker.accept_partial("seg_c", 2, 0, 100, "x")
    with pytest.raises(AsrStreamError) as error:
        tracker.accept_partial("seg_c", 2, 0, 110, "x")
    assert error.value.violation is AsrViolation.STALE_REVISION


def test_timestamp_regression_and_invalid_range_are_rejected() -> None:
    tracker = AsrStreamTracker()
    tracker.accept_partial("seg_d", 0, 0, 500, "x")

    with pytest.raises(AsrStreamError) as regression:
        tracker.accept_partial("seg_d", 1, 0, 400, "x")
    assert regression.value.violation is AsrViolation.TIMESTAMP_REGRESSION

    with pytest.raises(AsrStreamError) as invalid:
        tracker.accept_partial("seg_e", 0, 200, 100, "x")
    assert invalid.value.violation is AsrViolation.INVALID_RANGE


def test_energy_vad_threshold() -> None:
    vad = EnergyVad(threshold_dbfs=-50)
    assert not vad.is_speech(-60)
    assert not vad.is_speech(None)
    assert vad.is_speech(-40)


def test_segmenter_emits_started_ongoing_ended() -> None:
    segmenter = Segmenter(start_ms=40, end_silence_ms=60, frame_ms=20)
    events = []
    for index in range(10):
        events.extend(segmenter.push("loopback", -20.0, index * 20_000_000))

    kinds = [event.kind for event in events]
    assert VadEventKind.SPEECH_STARTED in kinds
    assert VadEventKind.SPEECH_ONGOING in kinds

    for index in range(10, 20):
        events.extend(segmenter.push("loopback", -80.0, index * 20_000_000))

    ended = [event for event in events if event.kind is VadEventKind.SPEECH_ENDED]
    assert len(ended) == 1
    segment = ended[0].segment
    assert segment is not None
    assert segment.source == "loopback"
    assert segment.frames > 0
    assert segment.start_ts_ns < segment.end_ts_ns


def test_short_speech_does_not_start_segment() -> None:
    segmenter = Segmenter(start_ms=120, frame_ms=20)
    events = []
    for index in range(3):
        events.extend(segmenter.push("loopback", -20.0, index * 20_000_000))
    assert all(event.kind is not VadEventKind.SPEECH_STARTED for event in events)
    assert not segmenter.is_active("loopback")


def test_ring_buffer_is_bounded_and_counts_drops() -> None:
    segmenter = Segmenter(ring_max_frames=10, start_ms=20, frame_ms=20)
    for index in range(50):
        segmenter.push("mic", -20.0, index * 20_000_000)

    assert segmenter.buffered_frames("mic") == 10
    assert segmenter.dropped_frames("mic") == 40


def test_loopback_and_mic_states_are_independent() -> None:
    segmenter = Segmenter(start_ms=20, end_silence_ms=40, frame_ms=20)
    for index in range(5):
        segmenter.push("loopback", -20.0, index * 20_000_000)
    assert segmenter.is_active("loopback")
    assert not segmenter.is_active("mic")

    events = segmenter.push("mic", -80.0, 0)
    assert segmenter.is_active("loopback")
    assert not segmenter.is_active("mic")
    assert not events


def test_timestamp_must_be_monotonic() -> None:
    segmenter = Segmenter()
    segmenter.push("loopback", -20.0, 100)
    with pytest.raises(ValueError):
        segmenter.push("loopback", -20.0, 50)


def test_flush_ends_active_segment() -> None:
    segmenter = Segmenter(start_ms=20, frame_ms=20)
    for index in range(5):
        segmenter.push("mic", -20.0, index * 20_000_000)

    events = segmenter.flush("mic", 200_000_000)
    assert events and events[0].kind is VadEventKind.SPEECH_ENDED
    assert not segmenter.is_active("mic")


def test_asr_error_does_not_change_capture_state() -> None:
    capture = CaptureStateMachine()
    capture.transition(CaptureState.STARTING)
    capture.transition(CaptureState.CAPTURING)

    asr = AsrStatusTracker()
    asr.connecting("deepseek")
    asr.ready()
    asr.fail("asr_unavailable", "provider down")

    assert asr.state is AsrState.ERROR
    assert capture.state is CaptureState.CAPTURING
    assert capture.error_code is None


def test_asr_payload_matches_schema() -> None:
    tracker = AsrStatusTracker()
    tracker.fail("asr_timeout", "timeout")
    event = parse_event(
        json.dumps(
            {
                "v": 1,
                "type": "asr.status",
                "eventId": "evt_01JAY0000000000000000000Z",
                "ts": 1,
                "payload": tracker.as_payload(),
            }
        )
    )
    assert event.type == "asr.status"
