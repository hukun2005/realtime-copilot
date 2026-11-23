"""P51-04：RAG 自动检索注入与失败降级。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from copilot_core.answering import AnswerRuntime, RagContextBuilder
from copilot_core.detection import QuestionEngine
from copilot_core.knowledge import KnowledgeChunk, KnowledgeStore, technical_chunk
from copilot_core.storage import close, open_database


def question(text: str = "为什么项目使用 Pinia？"):
    engine = QuestionEngine(generation=1)
    item, _ = engine.ingest_final(segment_id="s", source="loopback", text=text)
    assert item is not None
    return item


def chunk(identifier: str, *, content: str, source: str = "analysis") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=identifier,
        project_id="rp_1",
        repository_id="repo_1",
        commit_sha="abc",
        source_type=source,
        file_path="src/store/user.ts",
        language="ts",
        symbol_name="useUserStore",
        start_line=3,
        end_line=8,
        content=content,
    )


def test_builder_separates_evidence_and_technical(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        store = KnowledgeStore(connection)
        store.add(chunk("kc_p", content="Pinia 用户模块状态"))
        store.add(technical_chunk(topic="Pinia", content="Pinia 是 Vue 状态库"))
        context = RagContextBuilder(store).build(question(), "rp_1")
        assert context.project_evidence and "Pinia 用户模块状态" in context.project_evidence[0]
        assert context.technical_knowledge == ("Pinia 是 Vue 状态库",)
        assert context.citations[0]["commit_sha"] == "abc"
        assert context.citations[0]["start_line"] == 3
        assert context.missing == ()
    finally:
        close(connection)


def test_no_hits_marks_missing(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        context = RagContextBuilder(KnowledgeStore(connection)).build(question(), "rp_1")
        assert context.project_evidence == ()
        assert "project_evidence" in context.missing
    finally:
        close(connection)


def test_rag_failure_does_not_crash() -> None:
    class Broken:
        def search(self, *args, **kwargs):
            raise RuntimeError("db down")

    context = RagContextBuilder(Broken()).build(question(), "rp_1")
    assert context.project_evidence == ()
    assert context.missing == ("rag_failed",)


async def test_runtime_uses_rag_context(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        store = KnowledgeStore(connection)
        store.add(chunk("kc_p", content="Pinia 用户模块状态"))
        store.add(technical_chunk(topic="Pinia", content="Pinia 是 Vue 状态库"))
        captured = []

        class Provider:
            async def stream(self, *, question, context):
                captured.append(context)
                yield "ok"

        events = []

        async def publish(event):
            events.append(event)

        runtime = AnswerRuntime(
            provider=Provider(),
            publish=publish,
            context_factory=RagContextBuilder(store).build,
        )
        request_id = await runtime.submit_question(question(), session_id="s1", project_id="rp_1")
        for _ in range(200):
            if runtime.status(request_id)["state"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert runtime.status(request_id)["state"] == "completed"
        assert captured and captured[0].project_evidence
        assert captured[0].technical_knowledge
    finally:
        close(connection)
