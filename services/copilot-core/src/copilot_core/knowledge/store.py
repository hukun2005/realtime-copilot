"""RI-4：知识块与 SQLite FTS5 检索。"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

SOURCE_TYPES = {"resume", "repository", "analysis", "technical"}


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    project_id: str
    repository_id: str
    commit_sha: str
    source_type: str
    file_path: str
    language: str
    symbol_name: str
    start_line: int
    end_line: int
    content: str
    embedding_status: str = "pending"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


class KnowledgeStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, chunk: KnowledgeChunk) -> bool:
        if chunk.source_type not in SOURCE_TYPES:
            raise ValueError(f"invalid source_type: {chunk.source_type}")
        exists = self._connection.execute(
            "SELECT 1 FROM knowledge_chunks WHERE chunk_id = ?", (chunk.chunk_id,)
        ).fetchone()
        if exists is not None:
            return False
        self._connection.execute(
            """
            INSERT INTO knowledge_chunks(
                chunk_id, project_id, repository_id, commit_sha, source_type,
                file_path, language, symbol_name, start_line, end_line, content,
                content_hash, embedding_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.chunk_id,
                chunk.project_id,
                chunk.repository_id,
                chunk.commit_sha,
                chunk.source_type,
                chunk.file_path,
                chunk.language,
                chunk.symbol_name,
                str(chunk.start_line),
                str(chunk.end_line),
                chunk.content,
                chunk.content_hash,
                chunk.embedding_status,
            ),
        )
        return True

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        project_id: str | None = None,
        repository_id: str | None = None,
        commit_sha: str | None = None,
        source_type: str | None = None,
    ) -> list[dict[str, object]]:
        query = query.strip()
        if not query:
            return []
        clauses = ["knowledge_chunks MATCH ?"]
        params: list[object] = [_fts_query(query)]
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if repository_id:
            clauses.append("repository_id = ?")
            params.append(repository_id)
        if commit_sha:
            clauses.append("commit_sha = ?")
            params.append(commit_sha)
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        params.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT chunk_id, project_id, repository_id, commit_sha, source_type,
                   file_path, language, symbol_name, start_line, end_line, content,
                   bm25(knowledge_chunks) AS rank
            FROM knowledge_chunks
            WHERE {' AND '.join(clauses)}
            ORDER BY rank LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def _fts_query(query: str) -> str:
    escaped = query.replace('"', '""')
    return f'"{escaped}"'
