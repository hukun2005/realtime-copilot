"""SQLite 初始化与版本迁移。

迁移以 ``PRAGMA user_version`` 为准，单向、可重入；每次升级在一个事务内执行
一组语句，失败则整体回滚，不会留下半升级的库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: 版本号 → 该版本新增的语句（按顺序执行）。
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """,
    ),
    2: (
        """
        CREATE TABLE IF NOT EXISTS meeting_sessions (
            id TEXT PRIMARY KEY,
            generation INTEGER NOT NULL,
            started_at INTEGER NOT NULL,
            ended_at INTEGER,
            mode TEXT NOT NULL DEFAULT 'standalone',
            status TEXT NOT NULL DEFAULT 'active'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS transcript_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES meeting_sessions(id) ON DELETE CASCADE,
            generation INTEGER NOT NULL,
            segment_id TEXT NOT NULL,
            source TEXT NOT NULL,
            start_ts_ns INTEGER NOT NULL,
            end_ts_ns INTEGER NOT NULL,
            text TEXT NOT NULL,
            revision INTEGER NOT NULL,
            provider TEXT,
            created_at INTEGER NOT NULL,
            UNIQUE(session_id, segment_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_transcript_session_start
        ON transcript_segments(session_id, start_ts_ns)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_transcript_source_start
        ON transcript_segments(session_id, source, start_ts_ns)
        """,
    ),
    3: (
        """
        CREATE TABLE IF NOT EXISTS resumes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'parsed',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS resume_projects (
            id TEXT PRIMARY KEY,
            resume_id TEXT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
            project_name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '',
            tech_stack TEXT NOT NULL DEFAULT '[]',
            start_date TEXT,
            end_date TEXT,
            achievements TEXT NOT NULL DEFAULT '[]',
            github_urls TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.5,
            source_location TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_resume_projects_resume
        ON resume_projects(resume_id)
        """,
    ),
    4: (
        """
        CREATE TABLE IF NOT EXISTS repositories (
            id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL UNIQUE,
            private INTEGER NOT NULL DEFAULT 0,
            description TEXT NOT NULL DEFAULT '',
            language TEXT,
            html_url TEXT NOT NULL,
            default_branch TEXT NOT NULL,
            pushed_at TEXT,
            commit_sha TEXT,
            updated_at INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS project_repositories (
            project_id TEXT NOT NULL REFERENCES resume_projects(id) ON DELETE CASCADE,
            repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
            state TEXT NOT NULL DEFAULT 'DISCOVERED',
            score REAL NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(project_id, repository_id)
        )
        """,
    ),
    5: (
        """
        CREATE TABLE IF NOT EXISTS repository_snapshots (
            repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
            commit_sha TEXT NOT NULL,
            partial INTEGER NOT NULL DEFAULT 0,
            file_count INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(repository_id, commit_sha)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_reports (
            id TEXT PRIMARY KEY,
            repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
            commit_sha TEXT NOT NULL,
            analysis_version TEXT NOT NULL,
            report_json TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_reports_repo
        ON analysis_reports(repository_id, created_at DESC)
        """,
    ),
}

SCHEMA_VERSION = max(MIGRATIONS)


class StorageError(Exception):
    """数据库不可用或版本不兼容。"""


def connect(path: Path) -> sqlite3.Connection:
    """打开数据库并设置连接级 PRAGMA。

    Args:
        path: 数据库文件路径；父目录会被创建。

    Raises:
        StorageError: 无法打开文件。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    except sqlite3.Error as exc:  # pragma: no cover - 依赖文件系统错误
        raise StorageError(f"cannot open database: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def current_version(connection: sqlite3.Connection) -> int:
    """读取当前 schema 版本。"""
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def migrate(connection: sqlite3.Connection) -> int:
    """把数据库升级到 :data:`SCHEMA_VERSION`。

    Args:
        connection: 已打开的连接。

    Returns:
        升级后的版本号。

    Raises:
        StorageError: 数据库版本高于本程序支持的版本。
    """
    version = current_version(connection)
    if version > SCHEMA_VERSION:
        raise StorageError(
            f"database schema v{version} is newer than supported v{SCHEMA_VERSION}",
        )
    for target in sorted(MIGRATIONS):
        if target <= version:
            continue
        connection.execute("BEGIN")
        try:
            for statement in MIGRATIONS[target]:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {target}")
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        version = target
    return version


def open_database(path: Path) -> sqlite3.Connection:
    """打开并迁移数据库。

    Args:
        path: 数据库文件路径。
    """
    connection = connect(path)
    try:
        migrate(connection)
    except Exception:
        connection.close()
        raise
    return connection


def close(connection: sqlite3.Connection | None) -> None:
    """关闭连接；``None`` 安全。"""
    if connection is not None:
        connection.close()
