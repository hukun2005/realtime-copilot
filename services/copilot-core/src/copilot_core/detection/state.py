"""P4.4：问题状态机、去重与历史。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from .classifier import Detection, QuestionType


class QuestionState(StrEnum):
    COLLECTING = "collecting"
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    ANSWERING = "answering"
    ANSWERED = "answered"
    IGNORED = "ignored"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    FAILED = "failed"


ALLOWED: dict[QuestionState, set[QuestionState]] = {
    QuestionState.COLLECTING: {QuestionState.CANDIDATE, QuestionState.CANCELLED, QuestionState.FAILED},
    QuestionState.CANDIDATE: {
        QuestionState.CONFIRMED,
        QuestionState.IGNORED,
        QuestionState.SUPERSEDED,
        QuestionState.CANCELLED,
        QuestionState.FAILED,
    },
    QuestionState.CONFIRMED: {
        QuestionState.ANSWERING,
        QuestionState.SUPERSEDED,
        QuestionState.CANCELLED,
        QuestionState.IGNORED,
        QuestionState.FAILED,
    },
    QuestionState.ANSWERING: {QuestionState.ANSWERED, QuestionState.FAILED, QuestionState.CANCELLED},
    QuestionState.ANSWERED: set(),
    QuestionState.IGNORED: set(),
    QuestionState.CANCELLED: set(),
    QuestionState.SUPERSEDED: set(),
    QuestionState.FAILED: set(),
}


class InvalidTransition(ValueError):
    pass


@dataclass
class Question:
    text: str
    normalized_text: str
    question_type: QuestionType
    confidence: float
    source: str
    segment_id: str
    id: str = field(default_factory=lambda: "q_" + uuid.uuid4().hex)
    state: QuestionState = QuestionState.CANDIDATE
    parent_id: str | None = None
    parent_reason: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)

    def transition(self, target: QuestionState) -> None:
        if target not in ALLOWED[self.state]:
            raise InvalidTransition(f"{self.state} -> {target}")
        self.state = target
        self.updated_at = time.monotonic()


class QuestionStore:
    def __init__(self) -> None:
        self._questions: dict[str, Question] = {}
        self._current: str | None = None

    def add(
        self,
        detection: Detection,
        *,
        parent_id: str | None = None,
        parent_reason: str | None = None,
        follow_up: bool = False,
    ) -> Question | None:
        existing = self._find_similar(detection.normalized_text)
        if existing is not None:
            return existing
        if self._current is not None and not follow_up:
            current = self._questions[self._current]
            if current.state in (QuestionState.CANDIDATE, QuestionState.CONFIRMED):
                current.transition(QuestionState.SUPERSEDED)
        question = Question(
            text=detection.text,
            normalized_text=detection.normalized_text,
            question_type=detection.question_type,
            confidence=detection.confidence,
            source=detection.source,
            segment_id=detection.segment_id,
            parent_id=parent_id,
            parent_reason=parent_reason,
        )
        self._questions[question.id] = question
        if not follow_up:
            self._current = question.id
        return question

    def _find_similar(self, normalized: str) -> Question | None:
        for question in self._questions.values():
            if question.normalized_text == normalized and question.state not in (
                QuestionState.CANCELLED,
                QuestionState.IGNORED,
                QuestionState.SUPERSEDED,
                QuestionState.ANSWERED,
                QuestionState.FAILED,
            ):
                return question
        return None

    def get(self, question_id: str) -> Question | None:
        return self._questions.get(question_id)

    def current(self) -> Question | None:
        return self._questions.get(self._current) if self._current else None

    def history(self) -> list[Question]:
        return sorted(self._questions.values(), key=lambda item: item.created_at)

    def transition(self, question_id: str, target: QuestionState) -> Question:
        question = self._questions[question_id]
        question.transition(target)
        return question

    def cancel_all(self, *, except_states: tuple[QuestionState, ...] = (QuestionState.ANSWERED,)) -> int:
        cancelled = 0
        for question in self._questions.values():
            if question.state in except_states:
                continue
            if question.state in (QuestionState.CANDIDATE, QuestionState.CONFIRMED, QuestionState.ANSWERED) or question.state is QuestionState.COLLECTING:
                question.transition(QuestionState.CANCELLED)
                cancelled += 1
        self._current = None
        return cancelled
