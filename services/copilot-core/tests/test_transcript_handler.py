"""P34-04：transcript 协议与 ASR final 事件整合。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from copilot_core.storage import StorageError, close, open_database
from copilot_core.transcript import (
    TranscriptAssembler,
    TranscriptBuffer,
    TranscriptHandler,
    TranscriptStore,
)

from copilot_protocol import make_event

SESSION = "sess_20261122abcd"


class Recorder:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def __call__(self, event: Any) -> None:
        self.events.append(event)


def asr_final(
    segment_id: str,
    text: str,
    *,
    start_ms: int = 0,
    source: str = "loopback",
    revision: int = 1,
):
    return make_event(
        "asr.final",
        {
            "segmentId": segment_id,
            "source": source,
            "startTsNs": start_ms * 1_000_000,
            "endTsNs": (start_ms + 100) * 1_000_000,
            "revision": revision,
            "text": text,
        },
    )


def make_handler(tmp_path: Path, broadcast: Any | None = None) -> tuple[sqlite3.Connection, TranscriptHandler]:
    connection = open_database(tmp_path / "copilot.db")
    store = TranscriptStore(connection)
    handler = TranscriptHandler(
        assembler=TranscriptAssembler(generation=1),
        buffer=TranscriptBuffer(generation=1),
        store=store,
        broadcast=broadcast or Recorder(),
    )
    return connection, handler


async def test_final_is_persisted_and_broadcast(tmp_path: Path) -> None:
    recorder = Recorder()
    connection, handler = make_handler(tmp_path, recorder)
    try:
        await handler.start(SESSION, generation=1)
        await handler.handle(asr_final("seg_a", "大家好"))
        await handler.flush()

        segments = TranscriptStore(connection).segments(SESSION)
        assert [item.text for item in segments] == ["大家好"]
        types = [event.type for event in recorder.events]
        assert "transcript.segment" in types
        segment_event = next(event for event in recorder.events if event.type == "transcript.segment")
        assert segment_event.payload.session_id.root == SESSION
        assert segment_event.payload.text == "大家好"
        assert handler.persisted == 1
    finally:
        close(connection)


async def test_duplicate_final_is_emitted_once(tmp_path: Path) -> None:
    recorder = Recorder()
    connection, handler = make_handler(tmp_path, recorder)
    try:
        await handler.start(SESSION, generation=1)
        await handler.handle(asr_final("seg_a", "你好"))
        await handler.handle(asr_final("seg_a", "你好"))
        await handler.flush()

        assert len(TranscriptStore(connection).segments(SESSION)) == 1
        assert len([event for event in recorder.events if event.type == "transcript.segment"]) == 1
        assert handler.assembled == 1
    finally:
        close(connection)


async def test_non_final_events_are_ignored(tmp_path: Path) -> None:
    recorder = Recorder()
    connection, handler = make_handler(tmp_path, recorder)
    try:
        await handler.start(SESSION, generation=1)
        await handler.handle(make_event("asr.partial", {
            "segmentId": "seg_a",
            "source": "loopback",
            "startTsNs": 0,
            "endTsNs": 1,
            "revision": 1,
            "text": "你",
        }))
        await handler.handle({"type": "ping"})

        assert recorder.events == []
        assert TranscriptStore(connection).segments(SESSION) == []
    finally:
        close(connection)


async def test_stop_flushes_ends_session_and_resets(tmp_path: Path) -> None:
    recorder = Recorder()
    connection, handler = make_handler(tmp_path, recorder)
    try:
        await handler.start(SESSION, generation=1)
        await handler.handle(asr_final("seg_a", "最后一句"))
        await handler.stop(reason="capture_stopped")

        store = TranscriptStore(connection)
        assert [item.text for item in store.segments(SESSION)] == ["最后一句"]
        record = store.session(SESSION)
        assert record is not None and record.status == "completed" and record.ended_at is not None
        reset = next(event for event in recorder.events if event.type == "transcript.reset")
        assert reset.payload.reason == "capture_stopped"
        assert not handler.active
    finally:
        close(connection)


async def test_malformed_final_is_ignored(tmp_path: Path) -> None:
    connection, handler = make_handler(tmp_path)
    try:
        await handler.start(SESSION, generation=1)
        await handler.handle({"type": "asr.final", "payload": {"segmentId": "seg_x"}})

        assert handler.assembled == 0
    finally:
        close(connection)


async def test_broadcast_failure_does_not_break_persistence(tmp_path: Path) -> None:
    async def failing(_event: Any) -> None:
        raise RuntimeError("ui closed")

    connection, handler = make_handler(tmp_path, failing)
    try:
        await handler.start(SESSION, generation=1)
        await handler.handle(asr_final("seg_a", "仍然要落库"))
        await handler.flush()

        assert [item.text for item in TranscriptStore(connection).segments(SESSION)] == ["仍然要落库"]
        assert handler.persisted == 1
        assert handler.broadcast_errors >= 1
    finally:
        close(connection)


async def test_start_twice_ends_previous_generation(tmp_path: Path) -> None:
    recorder = Recorder()
    connection, handler = make_handler(tmp_path, recorder)
    try:
        await handler.start(SESSION, generation=1)
        await handler.start("sess_20261122efgh", generation=2)

        resets = [event for event in recorder.events if event.type == "transcript.reset"]
        assert len(resets) == 1
        assert resets[0].payload.reason == "generation_changed"
        assert handler.generation == 2
    finally:
        close(connection)


async def test_inactive_handler_ignores_final(tmp_path: Path) -> None:
    connection, handler = make_handler(tmp_path)
    try:
        await handler.handle(asr_final("seg_a", "没有会话"))
        assert handler.assembled == 0
    finally:
        close(connection)


async def test_snapshot_reports_counts_without_text(tmp_path: Path) -> None:
    connection, handler = make_handler(tmp_path)
    try:
        await handler.start(SESSION, generation=3)
        await handler.handle(asr_final("seg_a", "你好"))
        await handler.handle(asr_final("seg_a", "你好"))
        await handler.flush()

        snapshot = handler.snapshot()
        assert snapshot["active"] is True
        assert snapshot["sessionId"] == SESSION
        assert snapshot["generation"] == 3
        assert snapshot["assembled"] == 1
        assert snapshot["persisted"] == 1
        assert snapshot["assembler"]["duplicates"] == 1
        assert snapshot["buffer"]["segmentCount"] == 1
        assert "你好" not in repr(snapshot)
    finally:
        close(connection)


async def test_persist_failure_is_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, handler = make_handler(tmp_path)
    try:
        await handler.start(SESSION, generation=1)

        def fail(_session_id: str, _segment: Any) -> bool:
            raise StorageError("disk full")

        monkeypatch.setattr(handler._store, "append", fail)
        await handler.handle(asr_final("seg_a", "文本"))
        await handler.flush()

        assert handler.persist_errors == 1
        assert handler.persisted == 0
    finally:
        close(connection)


async def test_two_finals_are_broadcast_in_order(tmp_path: Path) -> None:
    recorder = Recorder()
    connection, handler = make_handler(tmp_path, recorder)
    try:
        await handler.start(SESSION, generation=1)
        await handler.handle(asr_final("seg_b", "第二句", start_ms=5_000))
        await handler.handle(asr_final("seg_a", "第一句", start_ms=0))
        await handler.flush()

        texts = [
            event.payload.text
            for event in recorder.events
            if event.type == "transcript.segment"
        ]
        assert texts == ["第一句", "第二句"]
    finally:
        close(connection)
