"""RI-5：项目证据与通用知识分离、引用与降级。"""

from __future__ import annotations

from pathlib import Path

from copilot_core.detection import QuestionEngine
from copilot_core.knowledge import KnowledgeChunk, KnowledgeStore, technical_chunk
from copilot_core.rag import build_rag_context, rag_answer_context
from copilot_core.storage import close, open_database


def chunk(identifier: str, *, project: str, source: str, content: str, path: str = "src/store.ts", line: int = 3) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=identifier,
        project_id=project,
        repository_id="repo_1",
        commit_sha="abc123",
        source_type=source,
        file_path=path,
        language="ts",
        symbol_name="useUserStore",
        start_line=line,
        end_line=line + 5,
        content=content,
    )


def question():
    engine = QuestionEngine(generation=1)
    item, _ = engine.ingest_final(segment_id="s", source="loopback", text="为什么项目使用 Pinia？")
    assert item is not None
    return item


def test_separates_project_and_technical_with_citations(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        store = KnowledgeStore(connection)
        store.add(chunk("kc_p", project="rp_1", source="analysis", content="Pinia 用户模块状态"))
        store.add(technical_chunk(topic="Pinia", content="Pinia 是 Vue 的状态管理库"))
        rag = build_rag_context(query="Pinia", store=store, project_id="rp_1")
        assert len(rag.project_evidence) == 1
        assert len(rag.technical_knowledge) == 1
        assert rag.citations[0]["commit_sha"] == "abc123"
        assert rag.citations[0]["start_line"] == 3
        assert "src/store.ts:3-8" in rag.project_evidence[0]
    finally:
        close(connection)


def test_project_filter_excludes_other_project(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        store = KnowledgeStore(connection)
        store.add(chunk("kc_1", project="rp_1", source="analysis", content="Pinia 项目一"))
        store.add(chunk("kc_2", project="rp_2", source="analysis", content="Pinia 项目二"))
        rag = build_rag_context(query="Pinia", store=store, project_id="rp_2")
        assert all("项目二" in item for item in rag.project_evidence)
    finally:
        close(connection)


def test_no_hits_marks_missing(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        rag = build_rag_context(query="不存在的主题", store=KnowledgeStore(connection), project_id="rp_1")
        assert rag.project_evidence == [] and rag.technical_knowledge == []
        assert "project_evidence" in rag.missing
        context = rag_answer_context(question=question(), rag=rag)
        assert context.project_evidence == ()
        assert context.technical_knowledge == ()
    finally:
        close(connection)
