"""RI-2：仓库元数据与项目关联（不保存 Token）。"""

from __future__ import annotations

import hashlib
import sqlite3
import time

from .client import Repository

STATES = {"DISCOVERED", "MATCHED", "CONFIRMED", "REJECTED", "ANALYZING", "READY", "STALE", "ERROR"}


class GitHubStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def upsert_repository(self, repository: Repository) -> str:
        repo_id = "repo_" + hashlib.sha1(repository.full_name.encode()).hexdigest()[:16]
        self._connection.execute(
            """
            INSERT INTO repositories(id, full_name, private, description, language, html_url,
                default_branch, pushed_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                description=excluded.description, language=excluded.language,
                html_url=excluded.html_url, default_branch=excluded.default_branch,
                pushed_at=excluded.pushed_at, updated_at=excluded.updated_at
            """,
            (
                repo_id,
                repository.full_name,
                int(repository.private),
                repository.description,
                repository.language,
                repository.html_url,
                repository.default_branch,
                repository.pushed_at,
                _now_ms(),
            ),
        )
        return repo_id

    def link(self, *, project_id: str, repository_id: str, score: float, reason: str, state: str = "DISCOVERED") -> None:
        if state not in STATES:
            raise ValueError(f"invalid state: {state}")
        self._connection.execute(
            """
            INSERT INTO project_repositories(project_id, repository_id, state, score, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, repository_id) DO UPDATE SET
                state=excluded.state, score=excluded.score, reason=excluded.reason, updated_at=excluded.updated_at
            """,
            (project_id, repository_id, state, score, reason, _now_ms()),
        )

    def set_state(self, *, project_id: str, repository_id: str, state: str) -> None:
        if state not in STATES:
            raise ValueError(f"invalid state: {state}")
        self._connection.execute(
            "UPDATE project_repositories SET state=?, updated_at=? WHERE project_id=? AND repository_id=?",
            (state, _now_ms(), project_id, repository_id),
        )

    def links(self, project_id: str) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT r.full_name, r.html_url, r.language, p.state, p.score, p.reason
            FROM project_repositories p JOIN repositories r ON r.id = p.repository_id
            WHERE p.project_id = ? ORDER BY p.score DESC
            """,
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def _now_ms() -> int:
    return int(time.time() * 1000)
