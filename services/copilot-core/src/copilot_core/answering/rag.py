"""P51-04：RAG 自动检索 → AnswerContext，失败可降级。"""

from __future__ import annotations

import re

from ..detection import AnswerContext, Question
from ..knowledge import KnowledgeStore
from ..rag import build_rag_context


class RagContextBuilder:
    def __init__(self, store: KnowledgeStore, *, limit: int = 6) -> None:
        self._store = store
        self._limit = limit

    def build(self, question: Question, project_id: str | None) -> AnswerContext:
        base = AnswerContext(
            question_id=question.id,
            question_text=question.text,
            question_type=question.question_type,
        )
        try:
            rag = build_rag_context(
                query=question.text,
                store=self._store,
                project_id=project_id,
                limit=self._limit,
            )
            if not rag.project_evidence and not rag.technical_knowledge:
                for token in _query_tokens(question.text):
                    rag = build_rag_context(
                        query=token,
                        store=self._store,
                        project_id=project_id,
                        limit=self._limit,
                    )
                    if rag.project_evidence or rag.technical_knowledge:
                        break
        except Exception:
            return AnswerContext(
                question_id=base.question_id,
                question_text=base.question_text,
                question_type=base.question_type,
                missing=("rag_failed",),
            )
        return AnswerContext(
            question_id=base.question_id,
            question_text=base.question_text,
            question_type=base.question_type,
            project_evidence=tuple(rag.project_evidence),
            technical_knowledge=tuple(rag.technical_knowledge),
            citations=tuple(rag.citations),
            missing=tuple(rag.missing),
        )


def _query_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_+#.\-]{2,}|[\u4e00-\u9fff]{3,}", text)
    return [token for token in tokens if token.lower() not in {"为什么", "项目", "使用"}]
