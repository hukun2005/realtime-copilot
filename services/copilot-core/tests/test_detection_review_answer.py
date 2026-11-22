"""P4：LLM Review / Answer 可插拔接口（不调用在线模型）。"""

from __future__ import annotations

import pytest
from copilot_core.detection import (
    AnswerResult,
    Detection,
    QuestionEngine,
    QuestionState,
    QuestionType,
    ReviewDecision,
    answer_question,
    apply_review,
)


class OverrideReviewer:
    def __init__(self, decision: ReviewDecision | None) -> None:
        self.decision = decision
        self.calls = 0

    def review(self, *, text: str, detection: Detection) -> ReviewDecision | None:
        self.calls += 1
        return self.decision


def test_review_can_override_type_and_confidence() -> None:
    engine = QuestionEngine(generation=1, reviewer=OverrideReviewer(ReviewDecision(question_type=QuestionType.SCENARIO, confidence=0.9, reason="mock")))
    question, detection = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包？")
    assert question is not None and detection is not None
    assert detection.question_type is QuestionType.SCENARIO
    assert detection.confidence == 0.9
    assert "review: mock" in detection.reason


def test_review_can_drop_question() -> None:
    engine = QuestionEngine(generation=1, reviewer=OverrideReviewer(ReviewDecision(is_question=False)))
    question, detection = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包？")
    assert question is None
    assert detection is not None and detection.is_question is False


def test_apply_review_without_decision_is_noop() -> None:
    detection = Detection(True, QuestionType.TECHNICAL, 0.8, "t", "t", "r", "loopback", "s")
    assert apply_review(detection, None) is detection


class FakeAnswer:
    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, *, question, context):
        self.calls += 1
        return AnswerResult(text="答案", provider="fake", latency_ms=12.0)


class FailingAnswer:
    async def answer(self, *, question, context):
        raise RuntimeError("llm unavailable")


async def test_answer_transitions_and_calls_provider() -> None:
    engine = QuestionEngine(generation=1)
    question, _ = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包？")
    assert question is not None
    engine.confirm(question.id)
    provider = FakeAnswer()

    result = await answer_question(question=question, provider=provider, context=[])

    assert result.text == "答案"
    assert question.state is QuestionState.ANSWERED
    assert provider.calls == 1


async def test_answer_failure_marks_failed() -> None:
    engine = QuestionEngine(generation=1)
    question, _ = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包？")
    assert question is not None
    engine.confirm(question.id)

    with pytest.raises(RuntimeError):
        await answer_question(question=question, provider=FailingAnswer())
    assert question.state is QuestionState.FAILED


async def test_answer_requires_confirmed_state() -> None:
    engine = QuestionEngine(generation=1)
    question, _ = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包？")
    assert question is not None
    with pytest.raises(ValueError):
        await answer_question(question=question, provider=FakeAnswer())
