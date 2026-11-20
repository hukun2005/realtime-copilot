"""SQLite 初始化与迁移。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from copilot_core.storage import (
    SCHEMA_VERSION,
    StorageError,
    close,
    connect,
    current_version,
    migrate,
    open_database,
)


def table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


def test_fresh_database_is_migrated(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        assert current_version(connection) == SCHEMA_VERSION
        names = table_names(connection)
        assert {"settings", "schema_meta"} <= names
    finally:
        close(connection)


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "copilot.db"
    first = open_database(path)
    close(first)

    second = connect(path)
    try:
        assert migrate(second) == SCHEMA_VERSION
        assert migrate(second) == SCHEMA_VERSION
    finally:
        close(second)


def test_newer_database_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "copilot.db"
    connection = connect(path)
    try:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
        with pytest.raises(StorageError):
            migrate(connection)
    finally:
        close(connection)


def test_settings_roundtrip(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        connection.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
            ("overlay_position", "bottom-right", 1763700000000),
        )
        row = connection.execute("SELECT value FROM settings WHERE key = ?", ("overlay_position",)).fetchone()
        assert row is not None
        assert row["value"] == "bottom-right"
    finally:
        close(connection)


def test_wal_mode_enabled(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
    finally:
        close(connection)
