"""P51-01：AnswerRuntime 生命周期、取消、抢占与隔离。"""

from __future__ import annotations

import asyncio

from copilot_core.answering import AnswerRuntime, RuntimeEvent
from copilot_core.detection import QuestionEngine


class FakeProvider:
    def __init__(self, chunks: list[str], delay: float = 0.0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.closed = False

    async def stream(self, *, question, context):
        try:
            for chunk in self.chunks:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield chunk
        finally:
            self.closed = True


class Recorder:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []
        self.terminal = asyncio.Event()

    async def publish(self, event: RuntimeEvent) -> None:
        self.events.append(event)
        if event.type in ("answer.completed", "answer.cancelled", "answer.failed"):
            self.terminal.set()

    def types(self) -> list[str]:
        return [event.type for event in self.events]


def question(text: str = "什么是闭包？"):
    engine = QuestionEngine(generation=1)
    item, _ = engine.ingest_final(segment_id=f"seg_{text}", source="loopback", text=text)
    assert item is not None
    return item


async def wait(runtime: AnswerRuntime, request_id: str, terminal: asyncio.Event) -> None:
    await asyncio.wait_for(terminal.wait(), timeout=2)
    assert runtime.status(request_id)["state"] in ("completed", "cancelled", "failed")


async def test_streams_and_terminates_once() -> None:
    recorder = Recorder()
    provider = FakeProvider(["你", "好"])
    runtime = AnswerRuntime(provider=provider, publish=recorder.publish)
    request_id = await runtime.submit_question(question(), session_id="s1")
    await wait(runtime, request_id, recorder.terminal)

    assert recorder.types() == ["answer.started", "answer.delta", "answer.delta", "answer.completed"]
    assert [event.sequence for event in recorder.events] == [1, 2, 3, 4]
    payload = recorder.events[-1].payload
    assert payload["answer"] == "你好"
    assert runtime.status(request_id)["state"] == "completed"


async def test_cancel_terminates_and_closes_upstream() -> None:
    recorder = Recorder()
    provider = FakeProvider(["a", "b", "c"], delay=0.02)
    runtime = AnswerRuntime(provider=provider, publish=recorder.publish)
    request_id = await runtime.submit_question(question(), session_id="s1")
    await asyncio.sleep(0.01)
    assert await runtime.cancel(request_id) is True
    await wait(runtime, request_id, recorder.terminal)
    assert runtime.status(request_id)["state"] == "cancelled"
    assert recorder.events[-1].type == "answer.cancelled"
    assert recorder.events[-1].payload["reason"] == "user_cancelled"


async def test_new_question_supersedes_old_generation() -> None:
    recorder = Recorder()
    runtime = AnswerRuntime(provider=FakeProvider(["old"], delay=0.03), publish=recorder.publish)
    first = await runtime.submit_question(question("什么是问题 A？"), session_id="s1")
    await asyncio.sleep(0.01)
    async def provider_for_new(question, context):
        yield "new"

    runtime._provider = type("P", (), {"stream": staticmethod(provider_for_new)})()
    second = await runtime.submit_question(question("什么是问题 B？"), session_id="s1")
    for _ in range(200):
        if runtime.status(second)["state"] == "completed":
            break
        await asyncio.sleep(0.01)

    assert second != first
    assert runtime.status(first)["state"] == "cancelled"
    assert runtime.status(second)["state"] == "completed"
    new_events = [event for event in recorder.events if event.request_id == second]
    assert {event.generation for event in new_events} == {2}
    old_types = [event.type for event in recorder.events if event.request_id == first]
    assert "answer.cancelled" in old_types
    assert "answer.completed" not in old_types


async def test_sessions_are_independent() -> None:
    recorder = Recorder()
    runtime = AnswerRuntime(provider=FakeProvider(["x"]), publish=recorder.publish)
    first = await runtime.submit_question(question("什么是问题 A？"), session_id="s1")
    second = await runtime.submit_question(question("什么是问题 B？"), session_id="s2")
    for _ in range(200):
        if runtime.status(first)["state"] == "completed" and runtime.status(second)["state"] == "completed":
            break
        await asyncio.sleep(0.01)
    assert runtime.status(first)["state"] == "completed"
    assert runtime.status(second)["state"] == "completed"
    assert runtime.status(first)["generation"] == 1
    assert runtime.status(second)["generation"] == 1


async def test_duplicate_submit_is_idempotent() -> None:
    recorder = Recorder()
    runtime = AnswerRuntime(provider=FakeProvider(["a"], delay=0.02), publish=recorder.publish)
    item = question()
    first = await runtime.submit_question(item, session_id="s1")
    second = await runtime.submit_question(item, session_id="s1")
    assert first == second


async def test_failure_has_terminal_event() -> None:
    class Broken:
        async def stream(self, *, question, context):
            raise RuntimeError("upstream")
            yield ""  # pragma: no cover

    recorder = Recorder()
    runtime = AnswerRuntime(provider=Broken(), publish=recorder.publish)
    request_id = await runtime.submit_question(question(), session_id="s1")
    await wait(runtime, request_id, recorder.terminal)
    assert runtime.status(request_id)["state"] == "failed"
    assert recorder.events[-1].type == "answer.failed"


async def test_cancel_session_and_cleanup() -> None:
    recorder = Recorder()
    runtime = AnswerRuntime(provider=FakeProvider(["a"], delay=0.03), publish=recorder.publish)
    first = await runtime.submit_question(question("什么是问题 A？"), session_id="s1")
    second = await runtime.submit_question(question("什么是问题 B？"), session_id="s1")
    assert await runtime.cancel_session("s1") == 1
    await wait(runtime, second, recorder.terminal)
    assert runtime.status(first)["state"] == "cancelled"
    for _ in range(50):
        if runtime.status(second)["state"] != "running":
            break
        await asyncio.sleep(0.01)
    assert runtime.cleanup(keep=0) >= 1
    assert runtime.status(second) is None

