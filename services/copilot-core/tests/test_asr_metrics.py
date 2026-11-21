"""P3.2.9：ASR 错误统计、bySource 与 Aliyun 指标口径。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from copilot_core.asr import (
    AsrProviderConfig,
    AsrResult,
    DashScopeParaformerProvider,
    MockAsrProvider,
    SpeechSegment,
)
from copilot_core.asr.manager import AsrManager
from copilot_core.asr.provider import AsrError
from copilot_core.asr.vad import SOURCES
from copilot_core.tickets import TicketIssuer


class FakePipeline:
    def __init__(self) -> None:
        self.queues = {source: asyncio.Queue() for source in SOURCES}

    async def get_segment(self, source: str) -> SpeechSegment:
        return await self.queues[source].get()

    def release_segment(self, item: SpeechSegment) -> None:
        return None


class Collector:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def __call__(self, event: object) -> None:
        self.events.append(event)


class FailingCollector:
    async def __call__(self, event: object) -> None:
        raise RuntimeError("broadcast failed")


def segment(segment_id: str, source: str = "loopback") -> SpeechSegment:
    return SpeechSegment(
        segment_id=segment_id,
        source=source,
        start_ts_ns=0,
        end_ts_ns=1,
        frames=1,
        pcm16=b"\x00" * 640,
        sample_rate=16000,
        channels=1,
        peak_dbfs=-20,
        rms_dbfs=-30,
    )


def result(segment_id: str, source: str, revision: int, *, final: bool) -> AsrResult:
    return AsrResult(
        segment_id=segment_id,
        source=source,
        revision=revision,
        text="文本",
        start_ts_ns=0,
        end_ts_ns=1,
        final=final,
    )


def make_manager(broadcast: Any | None = None) -> AsrManager:
    tickets = TicketIssuer()
    tickets.begin_session()
    return AsrManager(
        pipeline=FakePipeline(),
        tickets=tickets,
        provider=MockAsrProvider(),
        generation=1,
        broadcast=broadcast or Collector(),
    )


async def test_single_error_counts_once() -> None:
    manager = make_manager()

    await manager._publish_error("asr_timeout", "timeout")

    snapshot = manager.snapshot()
    assert snapshot["errors"] == 1
    assert snapshot["errorCodes"] == {"asr_timeout": 1}


async def test_same_error_twice_counts_twice() -> None:
    manager = make_manager()

    await manager._publish_error("asr_timeout", "timeout")
    await manager._publish_error("asr_timeout", "timeout")

    snapshot = manager.snapshot()
    assert snapshot["errors"] == 2
    assert snapshot["errorCodes"] == {"asr_timeout": 2}


async def test_different_codes_are_separate() -> None:
    manager = make_manager()

    await manager._publish_error("asr_timeout", "timeout")
    await manager._publish_error("asr_provider_error", "bad")

    snapshot = manager.snapshot()
    assert snapshot["errors"] == 2
    assert snapshot["errorCodes"] == {"asr_timeout": 1, "asr_provider_error": 1}


async def test_snapshot_matches_internal_counts() -> None:
    manager = make_manager()
    await manager._publish_error("asr_timeout", "timeout")

    snapshot = manager.snapshot()
    assert snapshot["errors"] == manager._errors
    assert snapshot["errorCodes"] == manager._error_codes


async def test_broadcast_failure_still_counts_error() -> None:
    manager = make_manager(FailingCollector())

    with pytest.raises(RuntimeError):
        await manager._publish_error("asr_timeout", "timeout")

    snapshot = manager.snapshot()
    assert snapshot["errors"] == 1
    assert snapshot["errorCodes"] == {"asr_timeout": 1}


async def test_by_source_counts_match_accepts() -> None:
    manager = make_manager()
    await manager.start()
    try:
        loop = segment("seg_loop", "loopback")
        mic = segment("seg_mic", "mic")
        await manager._accept(result("seg_loop", "loopback", 1, final=False), loop)
        await manager._accept(result("seg_loop", "loopback", 2, final=True), loop)
        await manager._accept(result("seg_mic", "mic", 1, final=True), mic)

        snapshot = manager.snapshot()
        assert snapshot["partials"] == 1
        assert snapshot["finals"] == 2
        assert snapshot["bySource"]["loopback"] == {"partials": 1, "finals": 1}
        assert snapshot["bySource"]["mic"] == {"partials": 0, "finals": 1}
    finally:
        await manager.stop()


class FakeSocket:
    def __init__(self, events: list[dict[str, Any]] | None = None, *, hold: bool = False) -> None:
        self._events = list(events or [])
        self.hold = hold
        self.closed = False
        self.sent: list[Any] = []
        self.task_id: str | None = None

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if self._events:
            return json.dumps(self._events.pop(0))
        if self.hold:
            await asyncio.sleep(5)
        raise RuntimeError("closed")

    async def close(self) -> None:
        self.closed = True


def aliyun_provider(
    socket: FakeSocket | None,
    *,
    fail: Exception | None = None,
    receive_timeout: float | None = None,
) -> DashScopeParaformerProvider:
    async def connect(*_args: Any, **_kwargs: Any) -> FakeSocket:
        if fail is not None:
            raise fail
        assert socket is not None
        return socket

    values: dict[str, Any] = {"name": "aliyun", "workspace_id": "ws-test", "api_key_env": "ASR_TEST_KEY"}
    if receive_timeout is not None:
        values["receive_timeout_seconds"] = receive_timeout
    config = AsrProviderConfig(**values)
    return DashScopeParaformerProvider(config, connect=connect)


def sentence(text: str, *, end: bool) -> dict[str, Any]:
    return {
        "header": {"event": "result-generated"},
        "payload": {"output": {"sentence": {"text": text, "sentence_end": end}}},
    }


async def test_failed_connection_counts_attempt_and_failure_only() -> None:
    provider = aliyun_provider(None, fail=RuntimeError("network down"))

    with pytest.raises(AsrError):
        [item async for item in provider.transcribe(segment("seg_x"))]

    metrics = provider.metrics()
    assert metrics["connection_attempts"] == 1
    assert metrics["connection_failures"] == 1
    assert metrics["connection_successes"] == 0
    assert metrics["active_web_sockets"] == 0


async def test_connect_validation_closes_socket_and_zeroes_active() -> None:
    socket = FakeSocket(
        [
            {"header": {"event": "task-started"}},
            {"header": {"event": "task-finished"}},
        ]
    )
    provider = aliyun_provider(socket)

    await provider.connect("ticket")

    assert socket.closed
    assert provider.metrics()["active_web_sockets"] == 0
    assert provider.status() == "ready"


async def test_timeout_releases_socket() -> None:
    provider = aliyun_provider(FakeSocket(hold=True), receive_timeout=0.05)

    with pytest.raises(AsrError):
        [item async for item in provider.transcribe(segment("seg_t"))]

    metrics = provider.metrics()
    assert metrics["timeout_count"] == 1
    assert metrics["active_web_sockets"] == 0


async def test_normal_path_has_no_reconnect_and_one_latency_sample() -> None:
    socket = FakeSocket(
        [
            {"header": {"event": "task-started"}},
            sentence("你好。", end=True),
            {"header": {"event": "task-finished"}},
        ]
    )
    provider = aliyun_provider(socket)
    provider._state = "ready"

    [item async for item in provider.transcribe(segment("seg_l"))]

    metrics = provider.metrics()
    assert metrics["reconnect_count"] == 0
    assert metrics["latencySamples"] == 1
    assert metrics["finalP50Ms"] > 0
    assert metrics["active_web_sockets"] == 0


async def test_connection_duration_is_handshake_only() -> None:
    """连接耗时只统计握手，不包含发送音频/识别等待。"""
    socket = FakeSocket(
        [
            {"header": {"event": "task-started"}},
            sentence("好", end=True),
            {"header": {"event": "task-finished"}},
        ]
    )
    provider = aliyun_provider(socket)
    provider._state = "ready"

    started = time.monotonic()
    [item async for item in provider.transcribe(segment("seg_d"))]
    elapsed_ms = (time.monotonic() - started) * 1000

    metric = provider.metrics()["connection_duration_ms"]
    assert metric < elapsed_ms + 5
    assert metric >= 0
