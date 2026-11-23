"""P51-03：Review 正常/拒绝/不确定/超时/坏 JSON。"""

from __future__ import annotations

import asyncio

from copilot_core.answering.review import ReviewService, ReviewStatus, deepseek_review_call
from copilot_core.detection import QuestionEngine


def question(text: str = "什么是闭包？"):
    engine = QuestionEngine(generation=1)
    item, detection = engine.ingest_final(segment_id="s", source="loopback", text=text)
    assert item is not None and detection is not None
    return item, detection


async def test_confirmed() -> None:
    async def call(text, detection, recent):
        return {
            "decision": "confirmed",
            "confidence": 0.92,
            "question_type": "technical",
            "reason": "明确技术问题",
            "normalized_question": "解释闭包",
        }

    item, detection = question()
    outcome = await ReviewService(call=call).review(question=item, detection=detection)
    assert outcome.decision is ReviewStatus.CONFIRMED
    assert outcome.confidence == 0.92
    assert outcome.question_type.value == "technical"


async def test_rejected_blocks_answer() -> None:
    async def call(text, detection, recent):
        return {"decision": "rejected", "confidence": 0.9, "question_type": "statement", "reason": "陈述", "normalized_question": "陈述"}

    item, detection = question()
    service = ReviewService(call=call)
    outcome = await service.review(question=item, detection=detection)
    assert service.should_answer(outcome, detection) is False


async def test_timeout_falls_back_to_rules() -> None:
    async def slow(text, detection, recent):
        await asyncio.sleep(0.05)
        return {"decision": "confirmed", "confidence": 1, "question_type": "technical", "reason": "x"}

    item, detection = question()
    service = ReviewService(call=slow, timeout_seconds=0.01)
    outcome = await service.review(question=item, detection=detection)
    assert outcome.decision is ReviewStatus.UNAVAILABLE
    assert outcome.review_status == "unavailable"
    assert service.should_answer(outcome, detection) is True


async def test_invalid_json_and_error_fall_back() -> None:
    async def bad(text, detection, recent):
        return "{not json"

    async def boom(text, detection, recent):
        raise RuntimeError("network")

    item, detection = question()
    for call in (bad, boom):
        outcome = await ReviewService(call=call).review(question=item, detection=detection)
        assert outcome.decision is ReviewStatus.UNAVAILABLE
        assert outcome.review_status == "unavailable"


async def test_uncertain_uses_confidence() -> None:
    async def call(text, detection, recent):
        return {"decision": "uncertain", "confidence": 0.4, "question_type": "general", "reason": "?", "normalized_question": "闭包"}

    item, detection = question("什么是闭包？")
    service = ReviewService(call=call)
    outcome = await service.review(question=item, detection=detection)
    assert outcome.decision is ReviewStatus.UNCERTAIN
    assert service.should_answer(outcome, detection) is (detection.confidence >= 0.55)


async def test_deepseek_review_call_uses_existing_complete() -> None:
    calls = []

    async def complete(messages, *, max_tokens=0):
        calls.append((messages, max_tokens))
        return '{"decision":"confirmed","confidence":0.9,"question_type":"technical","reason":"ok","normalized_question":"闭包"}'

    item, detection = question()
    service = ReviewService(call=deepseek_review_call(complete))
    outcome = await service.review(question=item, detection=detection)
    assert outcome.decision is ReviewStatus.CONFIRMED
    assert calls and "闭包" in calls[0][0][1]["content"]
