"""P4：DeepSeek Review/Answer 客户端（MockTransport，不访问网络）。"""

from __future__ import annotations

import httpx
import pytest
from copilot_core.detection import (
    DeepSeekAnswerProvider,
    DeepSeekClient,
    DeepSeekReviewer,
    Detection,
    LlmNotConfigured,
    QuestionEngine,
    QuestionType,
    ReviewDecision,
    apply_review,
    parse_review,
)


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


async def test_reviewer_parses_json_and_sends_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer test-key"
        return chat_response('{"is_question": true, "type": "scenario", "confidence": 0.88, "reason": "mock"}')

    client = DeepSeekClient(api_key="test-key", transport=httpx.MockTransport(handler))
    try:
        detection = Detection(True, QuestionType.TECHNICAL, 0.7, "t", "t", "rule", "loopback", "s")
        decision = await DeepSeekReviewer(client).review(text="如果让你设计呢？", detection=detection)
    finally:
        await client.close()

    assert decision is not None
    assert decision.question_type is QuestionType.SCENARIO
    assert decision.confidence == 0.88
    reviewed = apply_review(detection, decision)
    assert reviewed.question_type is QuestionType.SCENARIO


async def test_answer_provider_returns_result() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return chat_response("一句话答案。")

    client = DeepSeekClient(api_key="test-key", transport=httpx.MockTransport(handler))
    try:
        engine = QuestionEngine(generation=1)
        question, _ = engine.ingest_final(segment_id="s", source="loopback", text="什么是闭包？")
        assert question is not None
        engine.confirm(question.id)
        result = await DeepSeekAnswerProvider(client).answer(question=question)
    finally:
        await client.close()

    assert result.text == "一句话答案。"
    assert result.provider.startswith("deepseek:")


def test_parse_review_invalid_json_returns_none() -> None:
    assert parse_review("not json") is None
    assert parse_review("```json\n{}\n```") == ReviewDecision()


def test_missing_key_raises() -> None:
    with pytest.raises(LlmNotConfigured):
        DeepSeekClient(api_key="")


async def test_complete_does_not_retry_402() -> None:
    calls = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(402)

    client = DeepSeekClient(api_key="test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Exception) as error:
            await client.complete([{"role": "user", "content": "x"}])
        assert getattr(error.value, "code", None) == "payment_required"
        assert len(calls) == 1
    finally:
        await client.close()


async def test_complete_retries_429_then_succeeds() -> None:
    calls = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = DeepSeekClient(api_key="test", transport=httpx.MockTransport(handler))
    try:
        assert await client.complete([{"role": "user", "content": "x"}]) == "ok"
        assert len(calls) == 3
    finally:
        await client.close()
