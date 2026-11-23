"""P52-02：answer 事件双端类型消费。"""

from __future__ import annotations

import json
from pathlib import Path

from copilot_protocol import parse_event

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
ANSWER_FIXTURES = (
    "answer.started.json",
    "answer.delta.json",
    "answer.completed.json",
    "answer.cancelled.json",
    "answer.failed.json",
)


def test_answer_fixtures_parse_with_common_fields() -> None:
    types = []
    for name in ANSWER_FIXTURES:
        raw = (FIXTURES / name).read_text(encoding="utf-8")
        event = parse_event(raw)
        types.append(event.type)
        assert event.request_id.root.startswith("req_")
        assert event.session_id.root.startswith("sess_")
        assert event.question_id.root.startswith("q_")
        assert event.generation >= 0
        assert event.sequence >= 1
    assert types == [
        "answer.started",
        "answer.delta",
        "answer.completed",
        "answer.cancelled",
        "answer.failed",
    ]


def test_answer_fixtures_payloads_are_typed() -> None:
    started = parse_event((FIXTURES / "answer.started.json").read_text(encoding="utf-8"))
    assert started.payload.question_type.root in {
        "technical", "project", "behavioral", "scenario", "follow_up", "general", "statement",
    }
    completed = parse_event((FIXTURES / "answer.completed.json").read_text(encoding="utf-8"))
    assert completed.payload.usage is not None
    cancelled = json.loads((FIXTURES / "answer.cancelled.json").read_text(encoding="utf-8"))
    assert cancelled["payload"]["reason"] in {
        "user_cancelled", "superseded", "session_stopped", "shutdown", "timeout",
    }


