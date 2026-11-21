"""P3.2.4：阿里云 Paraformer Provider 协议与错误处理。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from copilot_core.asr import AsrProviderConfig, DashScopeParaformerProvider, SpeechSegment
from copilot_core.asr.provider import AsrError


class FakeSocket:
    def __init__(self, events: list[dict[str, Any]], *, hold: bool = False) -> None:
        self._events = list(events)
        self.sent: list[Any] = []
        self.closed = False
        self.hold = hold
        self.task_id: str | None = None

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if not self._events:
            if self.hold:
                await asyncio.sleep(5)
            raise RuntimeError("socket closed")
        return json.dumps(self._events.pop(0))

    async def close(self) -> None:
        self.closed = True


def started() -> dict[str, Any]:
    return {"header": {"event": "task-started"}}


def sentence(text: str, *, end: bool) -> dict[str, Any]:
    return {
        "header": {"event": "result-generated"},
        "payload": {"output": {"sentence": {"text": text, "sentence_end": end}}},
    }


def finished() -> dict[str, Any]:
    return {"header": {"event": "task-finished"}}


def segment() -> SpeechSegment:
    return SpeechSegment(
        segment_id="seg_cn",
        source="loopback",
        start_ts_ns=0,
        end_ts_ns=1_000_000_000,
        frames=50,
        pcm16=b"\x00" * 640,
        sample_rate=16000,
        channels=1,
        peak_dbfs=-12,
        rms_dbfs=-20,
    )


def make_provider(socket: FakeSocket, **overrides: Any) -> tuple[DashScopeParaformerProvider, list[str]]:
    urls: list[str] = []

    async def connect(url: str, **_kwargs: Any) -> FakeSocket:
        urls.append(url)
        return socket

    values: dict[str, Any] = {
        "name": "aliyun",
        "api_key_env": "ASR_TEST_KEY",
        "workspace_id": "ws-test",
    }
    values.update(overrides)
    config = AsrProviderConfig(**values)
    return DashScopeParaformerProvider(config, connect=connect), urls


async def test_connect_validates_and_closes() -> None:
    socket = FakeSocket([started(), finished()])
    provider, urls = make_provider(socket)

    await provider.connect("ticket")

    assert provider.status() == "ready"
    assert socket.closed
    assert urls and urls[0].startswith("wss://ws-test.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference")


async def test_transcribe_yields_partial_then_final() -> None:
    socket = FakeSocket(
        [
            started(),
            sentence("大家", end=False),
            sentence("大家好。", end=True),
            finished(),
        ]
    )
    provider, _ = make_provider(socket)

    results = [item async for item in provider.transcribe(segment())]

    assert [item.final for item in results] == [False, True]
    assert [item.revision for item in results] == [1, 2]
    assert results[-1].text == "大家好。"


async def test_empty_text_is_ignored() -> None:
    socket = FakeSocket([started(), sentence("", end=False), sentence("好", end=True), finished()])
    provider, _ = make_provider(socket)

    results = [item async for item in provider.transcribe(segment())]

    assert [item.text for item in results] == ["好"]


async def test_task_failed_maps_to_provider_error() -> None:
    socket = FakeSocket(
        [
            started(),
            {"header": {"event": "task-failed", "error_message": "bad audio"}},
        ]
    )
    provider, _ = make_provider(socket)

    with pytest.raises(AsrError) as error:
        [item async for item in provider.transcribe(segment())]
    assert error.value.code == "asr_provider_error"


async def test_handshake_auth_failure_is_not_retried() -> None:
    calls = 0

    async def connect(*_args: Any, **_kwargs: Any) -> FakeSocket:
        nonlocal calls
        calls += 1
        raise RuntimeError("HTTP 401")

    config = AsrProviderConfig(name="aliyun", workspace_id="ws-test", api_key_env="ASR_TEST_KEY")
    provider = DashScopeParaformerProvider(config, connect=connect)

    with pytest.raises(AsrError) as error:
        await provider.connect("ticket")
    assert error.value.code == "asr_unavailable"
    assert calls == 1


async def test_missing_workspace_is_rejected() -> None:
    socket = FakeSocket([started(), finished()])
    provider, _ = make_provider(socket, workspace_id=None)

    with pytest.raises(ValueError):
        await provider.connect("ticket")


async def test_cancel_closes_active_socket() -> None:
    socket = FakeSocket([started()], hold=True)
    provider, _ = make_provider(socket)
    task = asyncio.create_task(provider.transcribe(segment()).__anext__())
    await asyncio.sleep(0.05)

    await provider.cancel()
    await asyncio.sleep(0.05)

    assert socket.closed
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_metrics_count_segments() -> None:
    socket = FakeSocket([started(), sentence("好", end=True), finished()])
    provider, _ = make_provider(socket)

    [item async for item in provider.transcribe(segment())]

    metrics = provider.metrics()
    assert metrics["segment_count"] == 1
    assert metrics["connection_attempts"] == 1
    assert metrics["connection_successes"] == 1
