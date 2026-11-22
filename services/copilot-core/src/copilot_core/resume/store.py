"""RI-1：简历 SQLite 存储（v3）。"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

from ..storage import StorageError
from .models import Resume, ResumeProject, ResumeProjectUpdate, ResumeStatus


class ResumeStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, *, name: str, file_name: str, file_hash: str, raw_text: str) -> Resume:
        now = _now_ms()
        resume = Resume(
            id="res_" + uuid.uuid4().hex,
            name=name,
            file_name=file_name,
            file_hash=file_hash,
            raw_text=raw_text,
            created_at=now,
            updated_at=now,
            status=ResumeStatus.PARSED,
        )
        self._connection.execute(
            """
            INSERT INTO resumes(id, name, file_name, file_hash, raw_text, version, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resume.id,
                resume.name,
                resume.file_name,
                resume.file_hash,
                resume.raw_text,
                resume.version,
                str(resume.status),
                resume.created_at,
                resume.updated_at,
            ),
        )
        return resume

    def add_project(self, resume_id: str, project: ResumeProject) -> ResumeProject:
        try:
            self._connection.execute(
                """
                INSERT INTO resume_projects(
                    id, resume_id, project_name, description, role, tech_stack,
                    start_date, end_date, achievements, github_urls,
                    confidence, source_location, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.id,
                    resume_id,
                    project.project_name,
                    project.description,
                    project.role,
                    json.dumps(project.tech_stack, ensure_ascii=False),
                    project.start_date,
                    project.end_date,
                    json.dumps(project.achievements, ensure_ascii=False),
                    json.dumps(project.github_urls, ensure_ascii=False),
                    project.confidence,
                    project.source_location,
                    _now_ms(),
                    _now_ms(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise StorageError("resume does not exist") from error
        return project

    def get(self, resume_id: str) -> Resume | None:
        row = self._connection.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
        if row is None:
            return None
        return _resume(row, self.projects(resume_id))

    def projects(self, resume_id: str) -> list[ResumeProject]:
        rows = self._connection.execute(
            "SELECT * FROM resume_projects WHERE resume_id = ? ORDER BY created_at, id",
            (resume_id,),
        ).fetchall()
        return [_project(row) for row in rows]

    def list(self) -> list[Resume]:
        rows = self._connection.execute("SELECT * FROM resumes ORDER BY updated_at DESC").fetchall()
        return [_resume(row, self.projects(row["id"])) for row in rows]

    def delete(self, resume_id: str) -> bool:
        cursor = self._connection.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
        if cursor.rowcount > 0:
            self._connection.execute("DELETE FROM settings WHERE key='active_resume_id' AND value=?", (resume_id,))
        return cursor.rowcount > 0

    def update_project(self, project_id: str, update: ResumeProjectUpdate) -> ResumeProject | None:
        row = self._connection.execute("SELECT * FROM resume_projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        project = _project(row)
        data = project.model_dump()
        for key, value in update.model_dump(exclude_none=True).items():
            data[key] = value
        self._connection.execute(
            """
            UPDATE resume_projects SET project_name=?, description=?, role=?, tech_stack=?,
                start_date=?, end_date=?, achievements=?, github_urls=?, confidence=?, source_location=?,
                updated_at=?
            WHERE id=?
            """,
            (
                data["project_name"],
                data["description"],
                data["role"],
                json.dumps(data["tech_stack"], ensure_ascii=False),
                data["start_date"],
                data["end_date"],
                json.dumps(data["achievements"], ensure_ascii=False),
                json.dumps(data["github_urls"], ensure_ascii=False),
                data["confidence"],
                data["source_location"],
                _now_ms(),
                project_id,
            ),
        )
        row = self._connection.execute(
            "SELECT * FROM resume_projects WHERE id = ?", (project_id,)
        ).fetchone()
        return _project(row) if row is not None else None

    def set_active(self, resume_id: str) -> bool:
        if self.get(resume_id) is None:
            return False
        self._connection.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES('active_resume_id', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (resume_id, _now_ms()),
        )
        return True

    def active(self) -> Resume | None:
        row = self._connection.execute("SELECT value FROM settings WHERE key='active_resume_id'").fetchone()
        return self.get(row["value"]) if row is not None else None


def _resume(row: sqlite3.Row, projects: list[ResumeProject]) -> Resume:
    return Resume(
        id=row["id"],
        name=row["name"],
        file_name=row["file_name"],
        file_hash=row["file_hash"],
        raw_text=row["raw_text"],
        version=row["version"],
        status=ResumeStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        projects=projects,
    )


def _project(row: sqlite3.Row) -> ResumeProject:
    return ResumeProject(
        id=row["id"],
        resume_id=row["resume_id"],
        project_name=row["project_name"],
        description=row["description"],
        role=row["role"],
        tech_stack=json.loads(row["tech_stack"]),
        start_date=row["start_date"],
        end_date=row["end_date"],
        achievements=json.loads(row["achievements"]),
        github_urls=json.loads(row["github_urls"]),
        confidence=row["confidence"],
        source_location=row["source_location"],
    )


def _now_ms() -> int:
    return int(time.time() * 1000)
