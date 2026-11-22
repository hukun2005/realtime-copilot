"""P5：SSE 流式、取消与 AnswerContext。"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from copilot_core.detection import (
    AnswerContext,
    AnswerTaskManager,
    DeepSeekAnswerProvider,
    DeepSeekClient,
    LlmError,
    QuestionEngine,
    QuestionType,
    build_answer_context,
    build_answer_messages,
)


def sse(*chunks: str) -> bytes:
    return "".join(f"data: {chunk}\n\n" for chunk in chunks).encode()


async def test_stream_parses_chunks_and_done() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse(
                '{"choices":[{"delta":{"content":"你"}}]}',
                '{"choices":[{"delta":{"content":"好"}}]}',
                "[DONE]",
            ),
        )

    client = DeepSeekClient(api_key="test-key", transport=httpx.MockTransport(handler))
    try:
        chunks = [chunk async for chunk in client.stream([{"role": "user", "content": "hi"}])]
    finally:
        await client.close()
    assert chunks == ["你", "好"]


async def test_stream_invalid_json_raises() -> None:
    client = DeepSeekClient(
        api_key="test-key",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"data: nope\n\n")),
    )
    with pytest.raises(LlmError):
        [chunk async for chunk in client.stream([{"role": "user", "content": "hi"}])]
    await client.close()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_stream_http_errors_raise(status: int) -> None:
    client = DeepSeekClient(
        api_key="secret-key",
        transport=httpx.MockTransport(lambda _request: httpx.Response(status)),
    )
    with pytest.raises(LlmError):
        [chunk async for chunk in client.stream([{"role": "user", "content": "hi"}])]
    await client.close()


async def test_stream_network_error_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    client = DeepSeekClient(api_key="test-key", transport=httpx.MockTransport(handler))
    with pytest.raises(LlmError):
        [chunk async for chunk in client.stream([{"role": "user", "content": "hi"}])]
    await client.close()


async def test_stream_http_error_does_not_leak_key() -> None:
    client = DeepSeekClient(
        api_key="secret-key",
        transport=httpx.MockTransport(lambda _request: httpx.Response(401)),
    )
    with pytest.raises(LlmError) as error:
        [chunk async for chunk in client.stream([{"role": "user", "content": "hi"}])]
    assert "secret-key" not in str(error.value)
    assert "401" in str(error.value)
    await client.close()


class SlowProvider:
    def __init__(self, chunks: list[str], delay: float = 0.05) -> None:
        self.chunks = chunks
        self.delay = delay
        self.closed = False

    async def stream(self, *, question, context):
        try:
            for chunk in self.chunks:
                await asyncio.sleep(self.delay)
                yield chunk
        finally:
            self.closed = True


class FailingProvider:
    async def stream(self, *, question, context):
        raise ValueError("boom")
        yield ""  # pragma: no cover


def context() -> AnswerContext:
    return AnswerContext(
        question_id="q1",
        question_text="什么是闭包？",
        question_type=QuestionType.TECHNICAL,
    )


def question():
    engine = QuestionEngine(generation=1)
    item, _ = engine.ingest_final(segment_id="s", source="loopback", text="什么是闭包？")
    assert item is not None
    return item


async def test_task_manager_streams_and_completes() -> None:
    manager = AnswerTaskManager()
    deltas: list[str] = []
    done: list[str] = []
    task_id = await manager.start(
        provider=SlowProvider(["a", "b"], delay=0),
        question=question(),
        context=context(),
        on_delta=lambda chunk: _append(deltas, chunk),
        on_done=lambda text: _append(done, text),
        on_error=lambda code: _append(deltas, f"ERR:{code}"),
    )
    await _wait(manager)
    assert task_id.startswith("ans_")
    assert deltas == ["a", "b"]
    assert done == ["ab"]


async def test_new_task_cancels_old() -> None:
    manager = AnswerTaskManager()
    old: list[str] = []
    new: list[str] = []
    await manager.start(
        provider=SlowProvider(["o1", "o2", "o3"]),
        question=question(),
        context=context(),
        on_delta=lambda chunk: _append(old, chunk),
        on_done=lambda _text: _noop(),
        on_error=lambda _code: _noop(),
    )
    await asyncio.sleep(0.01)
    await manager.start(
        provider=SlowProvider(["n1", "n2"], delay=0),
        question=question(),
        context=context(),
        on_delta=lambda chunk: _append(new, chunk),
        on_done=lambda _text: _noop(),
        on_error=lambda _code: _noop(),
    )
    await _wait(manager)
    assert "o2" not in old and "o3" not in old
    assert new == ["n1", "n2"]


async def test_cancel_and_close_are_idempotent() -> None:
    manager = AnswerTaskManager()
    await manager.start(
        provider=SlowProvider(["a"]),
        question=question(),
        context=context(),
        on_delta=lambda _chunk: _noop(),
        on_done=lambda _text: _noop(),
        on_error=lambda _code: _noop(),
    )
    await manager.cancel()
    await manager.cancel()
    await manager.close()
    await manager.close()
    assert manager.running is False


async def test_provider_error_reports_code() -> None:
    manager = AnswerTaskManager()
    errors: list[str] = []
    await manager.start(
        provider=FailingProvider(),
        question=question(),
        context=context(),
        on_delta=lambda _chunk: _noop(),
        on_done=lambda _text: _noop(),
        on_error=lambda code: _append(errors, code),
    )
    await _wait(manager)
    assert errors == ["ValueError"]


def test_answer_context_separates_evidence_and_knowledge() -> None:
    item = question()
    ctx = build_answer_context(
        item,
        project_evidence=["store/user.ts: 使用 Pinia"],
        technical_knowledge=["Pinia 是 Vue 状态库"],
    )
    messages = build_answer_messages(ctx)
    text = "\n".join(message["content"] for message in messages)
    assert "项目证据" in text and "store/user.ts" in text
    assert "通用技术知识" in text and "Pinia 是 Vue 状态库" in text
    assert "无项目证据" not in text


async def test_deepseek_answer_provider_streams_context() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert b'"stream":true' in request.content
        return httpx.Response(200, content=sse('{"choices":[{"delta":{"content":"答案"}}]}', "[DONE]"))

    client = DeepSeekClient(api_key="test-key", transport=httpx.MockTransport(handler))
    try:
        provider = DeepSeekAnswerProvider(client)
        chunks = [
            chunk
            async for chunk in provider.stream(question=question(), context=context())
        ]
    finally:
        await client.close()
    assert chunks == ["答案"]


async def _append(items: list[str], value: str) -> None:
    items.append(value)


async def _noop() -> None:
    return None


async def _wait(manager: AnswerTaskManager) -> None:
    for _ in range(100):
        if not manager.running:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("task did not finish")
