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


def test_finalized_segment_cache_is_bounded() -> None:
    tracker = AsrStreamTracker(max_segments=2)
    for index in range(5):
        tracker.accept_final(f"seg_{index}", 0, 0, 100 + index, "x")

    assert len(tracker._segments) <= 2
    assert not tracker.is_finalized("seg_0")