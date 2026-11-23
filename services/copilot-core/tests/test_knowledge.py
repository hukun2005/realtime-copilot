"""RI-4：FTS5 知识库、来源隔离与检索过滤。"""

from __future__ import annotations

from pathlib import Path

from copilot_core.codeintel import analyze_repository
from copilot_core.knowledge import (
    KnowledgeChunk,
    KnowledgeStore,
    chunks_from_report,
    chunks_from_resume,
    technical_chunk,
)
from copilot_core.resume import Resume, ResumeProject
from copilot_core.storage import SCHEMA_VERSION, close, open_database


def chunk(identifier: str = "kc_1", *, project: str = "rp_1", source: str = "resume", content: str = "Pinia 状态管理") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=identifier,
        project_id=project,
        repository_id="repo_1",
        commit_sha="abc",
        source_type=source,
        file_path="src/store/user.ts",
        language="ts",
        symbol_name="useUserStore",
        start_line=1,
        end_line=10,
        content=content,
    )


def test_fts_search_and_filters(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        store = KnowledgeStore(connection)
        assert store.add(chunk("kc_1", content="Pinia 状态管理 用户模块"))
        assert store.add(chunk("kc_2", project="rp_2", source="analysis", content="Pinia 在结算页的使用"))
        assert store.add(technical_chunk(topic="Pinia", content="Pinia 是 Vue 状态库"))
        assert store.add(chunk("kc_1", content="duplicate")) is False

        hits = store.search("Pinia", limit=10)
        assert len(hits) == 3
        filtered = store.search("Pinia", project_id="rp_2")
        assert [item["chunk_id"] for item in filtered] == ["kc_2"]
        analysis = store.search("Pinia", source_type="analysis")
        assert len(analysis) == 1
        assert store.search("不存在的主题") == []
    finally:
        close(connection)


def test_resume_and_report_chunks_keep_source() -> None:
    resume = Resume(
        id="res_1",
        name="张三",
        file_name="r.docx",
        file_hash="h",
        raw_text="",
        created_at=0,
        updated_at=0,
        projects=[
            ResumeProject(
                id="rp_1",
                resume_id="res_1",
                project_name="面试助手",
                description="基于 Pinia 的状态管理",
                tech_stack=["Vue3", "Pinia"],
                github_urls=["https://github.com/a/b"],
            )
        ],
    )
    resume_chunks = chunks_from_resume(resume)
    assert len(resume_chunks) == 1
    assert resume_chunks[0].source_type == "resume"
    assert "Pinia" in resume_chunks[0].content

    report = analyze_repository(
        repository="a/b",
        commit_sha="abc",
        files={"src/store/user.ts": "defineStore('user', {})\n"},
    )
    report_chunks = chunks_from_report(report, project_id="rp_1", repository_id="repo_1")
    assert report_chunks
    assert report_chunks[0].commit_sha == "abc"
    assert report_chunks[0].file_path
