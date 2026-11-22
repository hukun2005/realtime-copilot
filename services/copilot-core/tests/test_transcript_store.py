"""P34-03：SQLite v2 迁移与 TranscriptStore。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from copilot_core.storage import SCHEMA_VERSION, StorageError, close, connect, open_database
from copilot_core.transcript import TranscriptSegment, TranscriptStore

MS = 1_000_000


def make_segment(
    segment_id: str,
    start_ms: int,
    text: str,
    *,
    source: str = "loopback",
    generation: int = 1,
    revision: int = 1,
) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=segment_id,
        source=source,
        start_ts_ns=start_ms * MS,
        end_ts_ns=(start_ms + 100) * MS,
        text=text,
        revision=revision,
        generation=generation,
        provider="paraformer",
    )


def open_store(tmp_path: Path) -> tuple[sqlite3.Connection, TranscriptStore]:
    connection = open_database(tmp_path / "copilot.db")
    store = TranscriptStore(connection)
    store.start_session("sess_1", generation=1)
    return connection, store


def test_v2_tables_and_indexes_exist(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {"meeting_sessions", "transcript_segments"} <= tables
        assert {"idx_transcript_session_start", "idx_transcript_source_start"} <= indexes
    finally:
        close(connection)


def test_v1_database_upgrades_and_keeps_settings(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    connection = connect(path)
    connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
    connection.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES ('overlay', 'bottom', 1)"
    )
    connection.execute("PRAGMA user_version = 1")
    close(connection)

    upgraded = open_database(path)
    try:
        row = upgraded.execute("SELECT value FROM settings WHERE key='overlay'").fetchone()
        assert row is not None and row["value"] == "bottom"
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        close(upgraded)


def test_start_session_is_idempotent(tmp_path: Path) -> None:
    connection, store = open_store(tmp_path)
    try:
        store.start_session("sess_1", generation=99)
        record = store.session("sess_1")
        assert record is not None
        assert record.generation == 1
        assert record.status == "active"
    finally:
        close(connection)


def test_append_and_read_ordered(tmp_path: Path) -> None:
    connection, store = open_store(tmp_path)
    try:
        assert store.append("sess_1", make_segment("seg_b", 3_000, "第二句"))
        assert store.append("sess_1", make_segment("seg_a", 0, "第一句"))

        segments = store.segments("sess_1")
        assert [item.segment_id for item in segments] == ["seg_a", "seg_b"]
        assert segments[0].text == "第一句"
        assert segments[0].provider == "paraformer"
        assert store.segment_count("sess_1") == 2
    finally:
        close(connection)


def test_duplicate_segment_is_idempotent(tmp_path: Path) -> None:
    connection, store = open_store(tmp_path)
    try:
        assert store.append("sess_1", make_segment("seg_a", 0, "你好"))
        assert not store.append("sess_1", make_segment("seg_a", 0, "你好"))
        assert store.segment_count("sess_1") == 1
    finally:
        close(connection)


def test_source_filter_and_limit(tmp_path: Path) -> None:
    connection, store = open_store(tmp_path)
    try:
        store.append("sess_1", make_segment("loop", 0, "远端"))
        store.append("sess_1", make_segment("mic", 1_000, "本地", source="mic"))
        store.append("sess_1", make_segment("loop2", 2_000, "远端2"))

        assert [item.segment_id for item in store.segments("sess_1", source="mic")] == ["mic"]
        assert len(store.segments("sess_1", limit=1)) == 1
    finally:
        close(connection)


def test_append_without_session_raises(tmp_path: Path) -> None:
    connection, store = open_store(tmp_path)
    try:
        with pytest.raises(StorageError):
            store.append("sess_missing", make_segment("seg_x", 0, "文本"))
    finally:
        close(connection)


def test_end_session_and_delete_cascade(tmp_path: Path) -> None:
    connection, store = open_store(tmp_path)
    try:
        store.append("sess_1", make_segment("seg_a", 0, "文本"))
        assert store.end_session("sess_1", status="completed", ended_at=123)
        record = store.session("sess_1")
        assert record is not None and record.ended_at == 123 and record.status == "completed"

        assert store.delete_session("sess_1")
        assert store.segments("sess_1") == []
        assert store.session("sess_1") is None
    finally:
        close(connection)


def test_no_audio_columns_are_persisted(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(transcript_segments)")
        }
        assert "text" in columns
        assert not any("audio" in name or "pcm" in name for name in columns)
    finally:
        close(connection)
