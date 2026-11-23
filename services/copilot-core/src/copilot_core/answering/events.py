"""P51-01：答案运行时的内部事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeEvent:
    type: str
    session_id: str
    request_id: str
    question_id: str
    generation: int
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "sessionId": self.session_id,
            "requestId": self.request_id,
            "questionId": self.question_id,
            "generation": self.generation,
            "sequence": self.sequence,
            "payload": self.payload,
        }
