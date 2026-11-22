"""P34-05：capture 生命周期与 transcript 会话接线。"""

from __future__ import annotations

from copilot_core.app import create_app
from copilot_core.storage import close, open_database
from copilot_core.transcript import TranscriptStore
from fastapi import FastAPI

from copilot_protocol import make_event


def asr_final(segment_id: str, text: str, *, start_ms: int = 0):
    return make_event(
        "asr.final",
        {
            "segmentId": segment_id,
            "source": "loopback",
            "startTsNs": start_ms * 1_000_000,
            "endTsNs": (start_ms + 100) * 1_000_000,
            "revision": 1,
            "text": text,
        },
    )


async def test_capture_start_creates_session_and_final_is_persisted(
    config, token, data_dir, monkeypatch
) -> None:
    monkeypatch.delenv("RTC_ASR_PROVIDER", raising=False)
    app: FastAPI = create_app(config, token=token, data_dir=data_dir, version="test")

    async with app.router.lifespan_context(app):
        handler = app.state.transcript_handler
        assert handler is not None
        assert not handler.active

        await app.state.transcript_start(1)
        assert handler.active
        session_id = handler.session_id
        assert session_id is not None

        await app.state.asr_events(asr_final("seg_a", "你好"))
        await handler.flush()

        store = TranscriptStore(app.state.db)
        assert [item.text for item in store.segments(session_id)] == ["你好"]
        record = store.session(session_id)
        assert record is not None and record.status == "active"

        await app.state.transcript_stop()
        assert not handler.active
        record = store.session(session_id)
        assert record is not None and record.status == "completed"


async def test_new_generation_ends_previous_session(config, token, data_dir, monkeypatch) -> None:
    monkeypatch.delenv("RTC_ASR_PROVIDER", raising=False)
    app: FastAPI = create_app(config, token=token, data_dir=data_dir, version="test")

    async with app.router.lifespan_context(app):
        handler = app.state.transcript_handler
        await app.state.transcript_start(1)
        first = handler.session_id
        await app.state.transcript_start(2)
        second = handler.session_id

        assert first is not None and second is not None and first != second
        store = TranscriptStore(app.state.db)
        first_record = store.session(first)
        second_record = store.session(second)
        assert first_record is not None and first_record.status == "completed"
        assert second_record is not None and second_record.status == "active"


async def test_shutdown_ends_active_session(config, token, data_dir, monkeypatch) -> None:
    monkeypatch.delenv("RTC_ASR_PROVIDER", raising=False)
    app: FastAPI = create_app(config, token=token, data_dir=data_dir, version="test")

    async with app.router.lifespan_context(app):
        handler = app.state.transcript_handler
        await app.state.transcript_start(1)
        session_id = handler.session_id
        assert session_id is not None

    connection = open_database(config.database_path(data_dir))
    try:
        record = TranscriptStore(connection).session(session_id)
        assert record is not None and record.status == "completed"
    finally:
        close(connection)


async def test_healthz_includes_transcript_metrics(config, token, data_dir, monkeypatch) -> None:
    monkeypatch.delenv("RTC_ASR_PROVIDER", raising=False)
    app: FastAPI = create_app(config, token=token, data_dir=data_dir, version="test")

    async with app.router.lifespan_context(app):
        await app.state.transcript_start(4)
        await app.state.asr_events(asr_final("seg_a", "指标"))
        handler = app.state.transcript_handler
        await handler.flush()

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body = (await client.get("/healthz")).json()
        assert body["transcript"]["active"] is True
        assert body["transcript"]["generation"] == 4
        assert body["transcript"]["persisted"] == 1
        assert body["transcript"]["assembler"]["accepted"] == 1


async def test_asr_events_are_ignored_when_transcript_idle(config, token, data_dir, monkeypatch) -> None:
    monkeypatch.delenv("RTC_ASR_PROVIDER", raising=False)
    app: FastAPI = create_app(config, token=token, data_dir=data_dir, version="test")

    async with app.router.lifespan_context(app):
        await app.state.asr_events(asr_final("seg_a", "没有会话"))
        assert app.state.transcript_handler.assembled == 0
