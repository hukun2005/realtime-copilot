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
