"""P52-04：RuntimeEvent 协议映射。"""

from __future__ import annotations

from copilot_core.answering import to_protocol
from copilot_core.answering.events import RuntimeEvent


def event(kind: str, payload: dict) -> RuntimeEvent:
    return RuntimeEvent(
        type=kind,
        session_id="sess_01JAY0000000000000000001",
        request_id="req_01JAY0000000000000000001",
        question_id="q_01JAY0000000000000000001",
        generation=1,
        sequence=1,
        payload=payload,
    )


def test_maps_all_answer_events() -> None:
    cases = [
        ("answer.started", {"question": "闭包", "questionType": "technical", "status": "running"}),
        ("answer.delta", {"delta": "x"}),
        ("answer.completed", {"answer": "x", "finishReason": "stop", "usage": None}),
        ("answer.cancelled", {"reason": "user_cancelled"}),
        ("answer.failed", {"code": "x", "message": "x", "retryable": False}),
    ]
    for kind, payload in cases:
        model = to_protocol(event(kind, payload))
        assert model.type == kind
        assert model.sequence == 1
        assert model.session_id.root.startswith("sess_")
