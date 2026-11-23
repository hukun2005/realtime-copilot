"""RI-5：项目证据 + 通用技术知识的 RAG 上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..detection import AnswerContext, Question
from ..knowledge import KnowledgeStore

PROJECT_SOURCES = {"resume", "repository", "analysis"}


@dataclass
class RagContext:
    project_evidence: list[str] = field(default_factory=list)
    technical_knowledge: list[str] = field(default_factory=list)
    citations: list[dict[str, object]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def build_rag_context(
    *,
    query: str,
    store: KnowledgeStore,
    project_id: str | None = None,
    limit: int = 6,
    max_evidence_chars: int = 3000,
) -> RagContext:
    hits = store.search(query, limit=limit, project_id=project_id)
    if project_id:
        hits += store.search(query, limit=limit, source_type="technical")
    seen: set[str] = set()
    context = RagContext()
    used = 0
    for hit in hits:
        chunk_id = str(hit["chunk_id"])
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        source = str(hit["source_type"])
        content = str(hit["content"])[:1200]
        if used + len(content) > max_evidence_chars:
            break
        if source == "technical":
            context.technical_knowledge.append(content)
        elif source in PROJECT_SOURCES:
            citation = {
                "chunk_id": chunk_id,
                "source_type": source,
                "repository_id": hit.get("repository_id") or "",
                "commit_sha": hit.get("commit_sha") or "",
                "file_path": hit.get("file_path") or "",
                "start_line": int(hit.get("start_line") or 0),
                "end_line": int(hit.get("end_line") or 0),
                "symbol_name": hit.get("symbol_name") or "",
            }
            context.project_evidence.append(_evidence_text(hit))
            context.citations.append(citation)
            used += len(content)
        else:
            continue
    if not context.project_evidence:
        context.missing.append("project_evidence")
    if not context.technical_knowledge:
        context.missing.append("technical_knowledge")
    return context


def rag_answer_context(
    *,
    question: Question,
    rag: RagContext,
    selected_projects: list[str] | None = None,
) -> AnswerContext:
    return AnswerContext(
        question_id=question.id,
        question_text=question.text,
        question_type=question.question_type,
        selected_projects=tuple(selected_projects or ()),
        project_evidence=tuple(rag.project_evidence),
        technical_knowledge=tuple(rag.technical_knowledge),
    )


def _evidence_text(hit: dict[str, object]) -> str:
    location = ""
    if hit.get("file_path"):
        location = f"{hit['file_path']}:{hit.get('start_line')}-{hit.get('end_line')}"
    symbol = str(hit.get("symbol_name") or "")
    prefix = f"[{location}] " if location else ""
    return f"{prefix}{symbol}: {hit.get('content')}"[:1200]
