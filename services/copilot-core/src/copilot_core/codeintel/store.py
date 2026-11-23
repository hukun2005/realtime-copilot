"""RI-3：分析快照与报告版本化存储。"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

from .analyzer import AnalysisReport


class CodeIntelStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save_snapshot(self, *, repository_id: str, commit_sha: str, partial: bool, file_count: int) -> None:
        self._connection.execute(
            """
            INSERT INTO repository_snapshots(repository_id, commit_sha, partial, file_count, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repository_id, commit_sha) DO UPDATE SET
                partial=excluded.partial, file_count=excluded.file_count, created_at=excluded.created_at
            """,
            (repository_id, commit_sha, int(partial), file_count, _now_ms()),
        )

    def save_report(self, *, repository_id: str, report: AnalysisReport) -> str:
        report_id = "ar_" + uuid.uuid4().hex
        self._connection.execute(
            """
            INSERT INTO analysis_reports(id, repository_id, commit_sha, analysis_version, report_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                repository_id,
                report.commit_sha,
                report.analysis_version,
                json.dumps(report.__dict__, ensure_ascii=False, default=lambda item: item.__dict__),
                _now_ms(),
            ),
        )
        return report_id

    def latest(self, repository_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT report_json FROM analysis_reports WHERE repository_id=? ORDER BY created_at DESC LIMIT 1",
            (repository_id,),
        ).fetchone()
        return json.loads(row["report_json"]) if row is not None else None


def _now_ms() -> int:
    return int(time.time() * 1000)
