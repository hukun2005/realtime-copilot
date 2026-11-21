"""P3.2.3：Deepgram 状态、URL 参数、Drain 与连接指标。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from copilot_core.asr import AsrAuthError, AsrProviderConfig, DeepgramProvider, SpeechSegment
from copilot_core.asr.provider import AsrError


class FakeSocket:
    def __init__(self, messages: list[tuple[float, dict[str, Any]]] | None = None, *, hold: bool = False) -> None:
        self._messages = list(messages or [])
        self.hold = hold
        self.closed = False
        self.sent: list[Any] = []

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if self._messages:
            delay, message = self._messages.pop(0)
            if delay:
                await asyncio.sleep(delay)
            return json.dumps(message)
        if self.hold:
            await asyncio.sleep(5)
        raise RuntimeError("socket closed")

    async def close(self) -> None:
        self.closed = True


def message(text: str, *, is_final: bool = False, speech_final: bool = False) -> dict[str, Any]:
    return {
        "is_final": is_final,
        "speech_final": speech_final,
        "channel": {"alternatives": [{"transcript": text}]},
    }


class Connector:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = outcomes
        self.calls = 0
        self.urls: list[str] = []

    async def __call__(self, url: str, **_kwargs: Any) -> Any:
        self.urls.append(url)
        index = min(self.calls, len(self._outcomes) - 1)
        self.calls += 1
        outcome = self._outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def segment(segment_id: str = "seg_1") -> SpeechSegment:
    return SpeechSegment(
        segment_id=segment_id,
        source="loopback",
        start_ts_ns=0,
        end_ts_ns=2_000_000_000,
        frames=50,
        pcm16=b"\x00" * 640,
        sample_rate=16000,
        channels=1,
        peak_dbfs=-12,
        rms_dbfs=-20,
    )


def make_provider(outcomes: list[Any], **overrides: Any) -> tuple[DeepgramProvider, Connector]:
    connector = Connector(outcomes)
    config = AsrProviderConfig(name="deepgram", api_key_env="ASR_TEST_KEY", **overrides)
    return DeepgramProvider(config, connect=connector), connector


async def collect(provider: DeepgramProvider) -> list[Any]:
    return [result async for result in provider.transcribe(segment())]


async def test_connect_reports_ready_only_after_real_socket() -> None:
    socket = FakeSocket()
    provider, connector = make_provider([socket])

    assert provider.status() == "idle"
    await provider.connect("ticket")

    assert provider.status() == "ready"
    assert connector.calls == 1
    assert socket.closed


async def test_connect_auth_failure_does_not_retry() -> None:
    provider, connector = make_provider([Exception("HTTP 401 Unauthorized")], max_retries=3)

    with pytest.raises(AsrAuthError):
        await provider.connect("ticket")

    assert connector.calls == 1
    assert provider.status() == "error"


async def test_connect_network_failure_is_not_ready() -> None:
    provider, _ = make_provider([Exception("network unreachable")])

    with pytest.raises(AsrError):
        await provider.connect("ticket")

    assert provider.status() == "error"


async def test_transcribe_retries_429_then_succeeds() -> None:
    provider, connector = make_provider(
        [
            Exception("HTTP 429 Too Many Requests"),
            Exception("HTTP 429 Too Many Requests"),
            FakeSocket([(0, message("你好", is_final=True, speech_final=True))]),
        ],
        max_retries=2,
        backoff_base_seconds=0.001,
    )
    provider._state = "ready"

    results = await collect(provider)

    assert results[-1].text == "你好"
    metrics = provider.metrics()
    assert metrics["reconnect_count"] == 2
    assert metrics["rate_limited_count"] == 2
    assert connector.calls == 3


async def test_transcribe_retries_5xx_then_succeeds() -> None:
    provider, connector = make_provider(
        [
            Exception("HTTP 503 Service Unavailable"),
            FakeSocket([(0, message("好", is_final=True, speech_final=True))]),
        ],
        max_retries=2,
        backoff_base_seconds=0.001,
    )
    provider._state = "ready"

    results = await collect(provider)

    assert results[-1].text == "好"
    assert provider.metrics()["reconnect_count"] == 1
    assert connector.calls == 2


async def test_unknown_option_is_rejected() -> None:
    provider, _ = make_provider([FakeSocket()], options={"bogus": "1"})

    with pytest.raises(ValueError):
        await provider.connect("ticket")


async def test_url_is_encoded_and_contains_configuration() -> None:
    provider, connector = make_provider(
        [FakeSocket()],
        model="a b+c",
        language="zh-CN",
        endpointing_ms=300,
        options={"tag": "x y"},
    )

    await provider.connect("ticket")

    url = connector.urls[0]
    assert "language=zh-CN" in url
    assert "model=a+b%2Bc" in url
    assert "endpointing=300" in url
    assert "tag=x+y" in url


async def test_drain_collects_late_text_into_single_final() -> None:
    socket = FakeSocket(
        [
            (0, message("你好", is_final=True, speech_final=True)),
            (0.05, message("世界", is_final=True)),
        ]
    )
    provider, _ = make_provider([socket])

    results = await collect(provider)

    finals = [item for item in results if item.final]
    assert len(finals) == 1
    assert finals[0].text == "你好世界"


async def test_drain_ends_on_metadata() -> None:
    socket = FakeSocket(
        [
            (0, message("你好", is_final=True, speech_final=True)),
            (0.05, {"type": "Metadata", "request_id": "r1"}),
        ]
    )
    provider, _ = make_provider([socket], settle_timeout_seconds=1.0)

    started = time.monotonic()
    results = await collect(provider)
    elapsed = time.monotonic() - started

    assert results[-1].text == "你好"
    assert elapsed < 0.8


async def test_drain_is_bounded_when_socket_stays_open() -> None:
    socket = FakeSocket([(0, message("你好", is_final=True, speech_final=True))], hold=True)
    provider, _ = make_provider([socket], settle_timeout_seconds=0.1)

    started = time.monotonic()
    results = await collect(provider)
    elapsed = time.monotonic() - started

    assert results[-1].text == "你好"
    assert elapsed < 1.0


async def test_cancel_during_drain_closes_socket() -> None:
    socket = FakeSocket([(0, message("你好", is_final=True, speech_final=True))], hold=True)
    provider, _ = make_provider([socket], settle_timeout_seconds=5.0)
    provider._state = "ready"

    task = asyncio.create_task(collect(provider))
    await asyncio.sleep(0.1)
    await provider.cancel()
    await asyncio.sleep(0.05)

    assert socket.closed
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_metrics_count_segments_and_connections() -> None:
    socket = FakeSocket([(0, message("好", is_final=True, speech_final=True))])
    provider, _ = make_provider([FakeSocket(), socket], settle_timeout_seconds=0.1)
    await provider.connect("ticket")
    provider._state = "ready"

    await collect(provider)

    metrics = provider.metrics()
    assert metrics["segment_count"] == 1
    assert metrics["connection_attempts"] >= 2
    assert metrics["connection_successes"] >= 2
    assert metrics["active_web_sockets"] == 0
