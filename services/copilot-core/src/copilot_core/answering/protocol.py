"""P52-04：RuntimeEvent → 协议事件。"""

from __future__ import annotations

import time
import uuid

from copilot_protocol.models import (
    AnswerCancelledEvent,
    AnswerCompletedEvent,
    AnswerDeltaEvent,
    AnswerFailedEvent,
    AnswerStartedEvent,
)

from .events import RuntimeEvent

EVENT_CLASSES = {
    "answer.started": AnswerStartedEvent,
    "answer.delta": AnswerDeltaEvent,
    "answer.completed": AnswerCompletedEvent,
    "answer.cancelled": AnswerCancelledEvent,
    "answer.failed": AnswerFailedEvent,
}


def to_protocol(event: RuntimeEvent):
    cls = EVENT_CLASSES[event.type]
    return cls.model_validate(
        {
            "v": 1,
            "type": event.type,
            "eventId": f"evt_{uuid.uuid4().hex}",
            "requestId": event.request_id,
            "sessionId": event.session_id,
            "questionId": event.question_id,
            "generation": event.generation,
            "sequence": event.sequence,
            "ts": int(time.time() * 1000),
            "payload": event.payload,
        }
    )
