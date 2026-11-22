"""转写持久化：会议会话与装配后的 Segment（SQLite）。

约束：

- 只存文本与元数据，不存原始音频、不存 API Key。
- 依赖 ``storage.db`` 的迁移（v2）；本模块不建表。
- 写入使用短事务；重复 ``(session_id, segment_id)`` 幂等忽略。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from ..storage import StorageError
from .assembler import TranscriptSegment


@dataclass(frozen=True)
class SessionRecord:
    id: str
    generation: int
    started_at: int
    ended_at: int | None
    mode: str
    status: str


class TranscriptStore:
    """SQLite 转写读写。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def start_session(
        self,
        session_id: str,
        *,
        generation: int,
        mode: str = "standalone",
        started_at: int | None = None,
    ) -> None:
        """创建会话；已存在则保持原记录（幂等）。"""
        self._connection.execute(
            """
            INSERT OR IGNORE INTO meeting_sessions(id, generation, started_at, mode, status)
            VALUES (?, ?, ?, ?, 'active')
            """,
            (session_id, generation, started_at or _now_ms(), mode),
        )

    def end_session(
        self,
        session_id: str,
        *,
        status: str = "completed",
        ended_at: int | None = None,
    ) -> bool:
        """结束会话；返回是否更新到行。"""
        cursor = self._connection.execute(
            "UPDATE meeting_sessions SET ended_at = ?, status = ? WHERE id = ?",
            (ended_at or _now_ms(), status, session_id),
        )
        return cursor.rowcount > 0

    def append(self, session_id: str, segment: TranscriptSegment) -> bool:
        """写入一条 Segment；重复返回 ``False``。"""
        try:
            cursor = self._connection.execute(
                """
                INSERT INTO transcript_segments(
                    session_id, generation, segment_id, source,
                    start_ts_ns, end_ts_ns, text, revision, provider, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, segment_id) DO NOTHING
                """,
                (
                    session_id,
                    segment.generation,
                    segment.segment_id,
                    segment.source,
                    segment.start_ts_ns,
                    segment.end_ts_ns,
                    segment.text,
                    segment.revision,
                    segment.provider,
                    _now_ms(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise StorageError("transcript session does not exist") from error
        return cursor.rowcount > 0

    def segments(
        self,
        session_id: str,
        *,
        source: str | None = None,
        limit: int | None = None,
    ) -> list[TranscriptSegment]:
        """按时间顺序读取会话内 Segment。"""
        sql = (
            "SELECT segment_id, source, start_ts_ns, end_ts_ns, text, revision, generation, provider "
            "FROM transcript_segments WHERE session_id = ?"
        )
        params: list[object] = [session_id]
        if source is not None:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY start_ts_ns, id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self._connection.execute(sql, params).fetchall()
        return [
            TranscriptSegment(
                segment_id=row["segment_id"],
                source=row["source"],
                start_ts_ns=row["start_ts_ns"],
                end_ts_ns=row["end_ts_ns"],
                text=row["text"],
                revision=row["revision"],
                generation=row["generation"],
                provider=row["provider"],
            )
            for row in rows
        ]

    def session(self, session_id: str) -> SessionRecord | None:
        row = self._connection.execute(
            "SELECT id, generation, started_at, ended_at, mode, status "
            "FROM meeting_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return _session(row) if row is not None else None

    def sessions(self, *, limit: int = 20) -> list[SessionRecord]:
        rows = self._connection.execute(
            "SELECT id, generation, started_at, ended_at, mode, status "
            "FROM meeting_sessions ORDER BY started_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_session(row) for row in rows]

    def segment_count(self, session_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM transcript_segments WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["count"]) if row is not None else 0

    def delete_session(self, session_id: str) -> bool:
        cursor = self._connection.execute("DELETE FROM meeting_sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0


def _session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        id=row["id"],
        generation=row["generation"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        mode=row["mode"],
        status=row["status"],
    )


def _now_ms() -> int:
    return int(time.time() * 1000)
