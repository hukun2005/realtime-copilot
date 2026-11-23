"""P51-05：完整 SSE 栈（Review → RAG → SSE）与取消。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from copilot_core.answering import build_answer_stack
from copilot_core.detection import QuestionEngine
from copilot_core.knowledge import KnowledgeStore
from copilot_core.storage import close, open_database


def question():
    engine = QuestionEngine(generation=1)
    item, _ = engine.ingest_final(segment_id="s", source="loopback", text="请解释 Vue3 的响应式原理。")
    assert item is not None
    return item


class Recorder:
    def __init__(self) -> None:
        self.events = []
        self.done = asyncio.Event()

    async def publish(self, event) -> None:
        self.events.append(event)
        if event.type in ("answer.completed", "answer.cancelled", "answer.failed"):
            self.done.set()


def handler(streams: list[dict], review: dict):
    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload.get("stream"):
            streams.append(payload)
            body = (
                'data: {"choices":[{"delta":{"content":"Vue3 "}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"使用 Proxy。"}}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, content=body.encode())
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(review, ensure_ascii=False)}}]})

    return respond


async def test_full_stack_streams_and_completes(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    streams: list[dict] = []
    recorder = Recorder()
    review = {
        "decision": "confirmed",
        "confidence": 0.95,
        "question_type": "technical",
        "reason": "技术问题",
        "normalized_question": "解释 Vue3 响应式原理",
    }
    stack = build_answer_stack(
        store=KnowledgeStore(connection),
        publish=recorder.publish,
        api_key="test",
        transport=httpx.MockTransport(handler(streams, review)),
    )
    try:
        request_id = await stack.runtime.submit_question(question(), session_id="s1")
        await asyncio.wait_for(recorder.done.wait(), timeout=2)
        assert stack.runtime.status(request_id)["state"] == "completed"
        assert [event.type for event in recorder.events] == [
            "answer.started",
            "answer.delta",
            "answer.delta",
            "answer.completed",
        ]
        assert recorder.events[-1].payload["answer"] == "Vue3 使用 Proxy。"
        assert len(streams) == 1
    finally:
        await stack.close()
        close(connection)


async def test_rejected_review_never_calls_sse(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    streams: list[dict] = []
    recorder = Recorder()
    review = {
        "decision": "rejected",
        "confidence": 0.9,
        "question_type": "statement",
        "reason": "不是问题",
        "normalized_question": "拒绝",
    }
    stack = build_answer_stack(
        store=KnowledgeStore(connection),
        publish=recorder.publish,
        api_key="test",
        transport=httpx.MockTransport(handler(streams, review)),
    )
    try:
        request_id = await stack.runtime.submit_question(question(), session_id="s1")
        await asyncio.wait_for(recorder.done.wait(), timeout=2)
        assert stack.runtime.status(request_id)["state"] == "cancelled"
        assert recorder.events[-1].payload["reason"] == "review_rejected"
        assert streams == []
    finally:
        await stack.close()
        close(connection)
