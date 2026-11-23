"""P51-01：实时问答任务模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

from ..detection import Question


class TaskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL = {TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED}


@dataclass
class AnswerTask:
    request_id: str
    session_id: str
    question: Question
    generation: int
    state: TaskState = TaskState.QUEUED
    sequence: int = 0
    text: str = ""
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def finish(self, state: TaskState) -> None:
        if state not in TERMINAL:
            raise ValueError(f"not terminal: {state}")
        self.state = state
        self.finished_at = time.monotonic()
