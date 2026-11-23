"""P51-02：ASR Final → QuestionEngine → AnswerRuntime。"""

from __future__ import annotations

from copilot_core.app import create_app
from fastapi import FastAPI

from copilot_protocol import make_event


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def submit_question(self, question, *, session_id: str, project_id=None):
        self.calls.append((question.id, session_id))
        return "req_" + question.id


def final(text: str, *, segment: str = "seg_1", source: str = "loopback"):
    return make_event(
        "asr.final",
        {
            "segmentId": segment,
            "source": source,
            "startTsNs": 0,
            "endTsNs": 1,
            "revision": 1,
            "text": text,
        },
    )


async def test_final_triggers_runtime_with_dedup(config, token, data_dir, monkeypatch) -> None:
    monkeypatch.delenv("RTC_ASR_PROVIDER", raising=False)
    app: FastAPI = create_app(config, token=token, data_dir=data_dir, version="test")
    runtime = FakeRuntime()
    app.state.answer_runtime = runtime

    async with app.router.lifespan_context(app):
        event = final("什么是 Vue3？")
        await app.state.asr_events(event)
        await app.state.asr_events(event)  # 同一 event/segment 重复
        assert len(runtime.calls) == 1

        await app.state.asr_events(final("什么是 Vue3？", segment="seg_2"))
        assert len(runtime.calls) == 2  # 重复问题但新 segment 允许

        await app.state.asr_events(final("什么是 Vue3？", segment="seg_1", source="mic"))
        assert len(runtime.calls) == 3  # 来源隔离


def test_guard_rejects_invalid_and_dedups() -> None:
    from copilot_core.answering import FinalGuard, is_meaningful

    assert not is_meaningful("")
    assert not is_meaningful("？？？")
    assert not is_meaningful("嗯")
    assert not is_meaningful("a")
    guard = FinalGuard()
    assert guard.accept(event_id="e1", session_id="s", source="loopback", segment_id="1", text="什么是闭包？")
    assert not guard.accept(event_id="e1", session_id="s", source="loopback", segment_id="1", text="什么是闭包？")
    assert not guard.accept(event_id="e2", session_id="s", source="loopback", segment_id="1", text="新文本")
    assert guard.accept(event_id="e3", session_id="s", source="mic", segment_id="1", text="什么是闭包？")


async def test_runtime_failure_does_not_break_detection(config, token, data_dir, monkeypatch) -> None:
    monkeypatch.delenv("RTC_ASR_PROVIDER", raising=False)
    app: FastAPI = create_app(config, token=token, data_dir=data_dir, version="test")

    class Broken:
        async def submit_question(self, *args, **kwargs):
            raise RuntimeError("runtime down")

    app.state.answer_runtime = Broken()
    async with app.router.lifespan_context(app):
        await app.state.asr_events(final("什么是闭包？"))
        assert app.state.detection.metrics()["questions"] == 1

