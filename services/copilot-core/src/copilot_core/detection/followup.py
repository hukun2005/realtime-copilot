"""P4.3：追问与主问题的上下文关联。"""

from __future__ import annotations

import time

from .classifier import Detection, QuestionType


class FollowUpResolver:
    """保存每来源最近的主问题，关联显式追问。"""

    def __init__(self, *, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._current: dict[str, tuple[str, float]] = {}

    def has_context(self, source: str, *, now: float | None = None) -> bool:
        return self._get(source, now=now) is not None

    def resolve(
        self,
        detection: Detection,
        *,
        now: float | None = None,
    ) -> tuple[str | None, str]:
        if detection.question_type is not QuestionType.FOLLOW_UP:
            return None, "not a follow-up"
        current = self._get(detection.source, now=now)
        if current is None:
            return None, "no active main question or context expired"
        return current, f"linked to {current} within {self.timeout_seconds:.0f}s"

    def register(self, *, source: str, question_id: str, question_type: QuestionType, now: float | None = None) -> None:
        if question_type is QuestionType.FOLLOW_UP:
            return
        self._current[source] = (question_id, now if now is not None else time.monotonic())

    def clear(self, source: str | None = None) -> None:
        if source is None:
            self._current.clear()
        else:
            self._current.pop(source, None)

    def _get(self, source: str, *, now: float | None = None) -> str | None:
        item = self._current.get(source)
        if item is None:
            return None
        question_id, created = item
        if (now if now is not None else time.monotonic()) - created > self.timeout_seconds:
            self._current.pop(source, None)
            return None
        return question_id
