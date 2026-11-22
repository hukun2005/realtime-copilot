"""P4.1–P4.5：转写、规则检测、追问、状态机与手动入口。"""

from __future__ import annotations

import pytest
from copilot_core.detection import (
    InvalidTransition,
    QuestionEngine,
    QuestionState,
    QuestionType,
    TranscriptLog,
    detect_question,
)


def test_partial_updates_and_final_freezes() -> None:
    log = TranscriptLog(generation=1)
    assert log.ingest_partial(segment_id="s1", source="loopback", text="什么") is not None
    entry = log.ingest_partial(segment_id="s1", source="loopback", text="什么是 Vue3?")
    assert entry is not None and entry.revision == 2
    final = log.ingest_final(segment_id="s1", source="loopback", text="什么是 Vue3?")
    assert final is not None and final.is_final
    assert log.ingest_partial(segment_id="s1", source="loopback", text="迟到") is None
    assert log.ingest_partial(segment_id="s1", source="loopback", text="迟到") is None


def test_partial_never_detects() -> None:
    engine = QuestionEngine(generation=1)
    engine.ingest_partial(segment_id="s1", source="loopback", text="什么是 Vue3?")
    assert engine.store.history() == []


def test_final_detected() -> None:
    engine = QuestionEngine(generation=1)
    question, detection = engine.ingest_final(
        segment_id="s1", source="loopback", text="什么是 Vue3?"
    )
    assert detection is not None and detection.is_question
    assert question is not None
    assert question.question_type is QuestionType.TECHNICAL


def test_duplicate_final_is_ignored() -> None:
    engine = QuestionEngine(generation=1)
    first, _ = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包?")
    second, detection = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包?")
    assert first is not None and second is None and detection is None


def test_old_generation_is_ignored() -> None:
    log = TranscriptLog(generation=2)
    assert log.ingest_final(segment_id="s1", source="loopback", text="旧", generation=1) is None


def test_sources_are_independent() -> None:
    log = TranscriptLog(generation=1)
    log.ingest_final(segment_id="same", source="loopback", text="远端")
    log.ingest_final(segment_id="same", source="mic", text="本地")
    assert len(log.recent(10)) == 2


def test_recent_and_trim() -> None:
    log = TranscriptLog(generation=1, max_entries=2)
    for index in range(3):
        log.ingest_final(segment_id=f"s{index}", source="loopback", text=f"文本{index}")
    assert [item.text for item in log.recent(10)] == ["文本1", "文本2"]


def test_overlong_text_is_clipped() -> None:
    log = TranscriptLog(generation=1)
    entry = log.ingest_final(segment_id="s1", source="loopback", text="字" * 5000)
    assert entry is not None and len(entry.text) == 4000


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Vue3 和 Vue2 有什么区别？", QuestionType.TECHNICAL),
        ("你在项目中如何做权限管理？", QuestionType.PROJECT),
        ("你遇到过最大的团队冲突是什么？", QuestionType.BEHAVIORAL),
        ("如果让你设计一个秒杀系统，你会怎么做？", QuestionType.SCENARIO),
    ],
)
def test_question_types(text: str, expected: QuestionType) -> None:
    detection = detect_question(text=text, source="loopback", segment_id="s")
    assert detection.is_question and detection.question_type is expected


def test_statement_is_not_question() -> None:
    detection = detect_question(text="我昨天完成了一个项目。", source="loopback", segment_id="s")
    assert detection.is_question is False
    assert detection.question_type is QuestionType.STATEMENT


def test_follow_up_links_to_main() -> None:
    engine = QuestionEngine(generation=1)
    main, _ = engine.ingest_final(segment_id="s1", source="loopback", text="什么是 Vue3 的响应式原理？")
    follow, detection = engine.ingest_final(
        segment_id="s2", source="loopback", text="它和 Vue2 有什么区别？"
    )
    assert main is not None and follow is not None
    assert detection is not None and detection.question_type is QuestionType.FOLLOW_UP
    assert follow.parent_id == main.id
    assert "linked" in (follow.parent_reason or "")


def test_follow_up_without_context_is_not_linked() -> None:
    engine = QuestionEngine(generation=1)
    question, _ = engine.ingest_final(segment_id="s1", source="loopback", text="它和 Vue2 有什么区别？")
    assert question is not None and question.parent_id is None


def test_new_main_supersedes_previous() -> None:
    engine = QuestionEngine(generation=1)
    first, _ = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包？")
    second, _ = engine.ingest_final(segment_id="s2", source="loopback", text="什么是事件循环？")
    assert first is not None and second is not None
    assert first.state is QuestionState.SUPERSEDED
    assert second.state is QuestionState.CANDIDATE


def test_state_transitions_and_invalid() -> None:
    engine = QuestionEngine(generation=1)
    question, _ = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包？")
    assert question is not None
    assert engine.confirm(question.id).state is QuestionState.CONFIRMED
    engine.store.transition(question.id, QuestionState.ANSWERING)
    assert engine.answer(question.id).state is QuestionState.ANSWERED
    with pytest.raises(InvalidTransition):
        engine.confirm(question.id)


def test_similar_question_is_deduplicated() -> None:
    engine = QuestionEngine(generation=1)
    first, _ = engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包？")
    second, _ = engine.ingest_final(segment_id="s2", source="loopback", text="什么是闭包？")
    assert first is not None and second is not None and second.id == first.id


def test_invalid_source_and_empty_text_are_ignored() -> None:
    engine = QuestionEngine(generation=1)
    assert engine.ingest_final(segment_id="s1", source="speaker", text="什么是闭包？") == (None, None)
    assert engine.ingest_final(segment_id="s2", source="loopback", text="   ") == (None, None)


def test_detector_exception_does_not_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    import copilot_core.detection.engine as engine_module

    engine = QuestionEngine(generation=1)

    def boom(**_kwargs):
        raise RuntimeError("detector failed")

    monkeypatch.setattr(engine_module, "detect_question", boom)
    assert engine.ingest_final(segment_id="s1", source="loopback", text="什么是闭包？") == (None, None)
    assert engine.last_error is not None


def test_cancel_all_and_high_frequency() -> None:
    engine = QuestionEngine(generation=1)
    for index in range(200):
        engine.ingest_final(segment_id=f"s{index}", source="loopback", text=f"什么是问题{index}？")
    assert engine.cancel_all() >= 0
    assert engine.store.current() is None


def test_manual_entry_uses_same_path() -> None:
    engine = QuestionEngine(generation=1)
    question, detection = engine.submit_text("请介绍一下你的实习经历。")
    assert detection is not None and detection.is_question
    assert question is not None and question.question_type is QuestionType.PROJECT
