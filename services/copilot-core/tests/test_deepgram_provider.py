"""P3.2.1：Deepgram final 累积、revision、取消与超时。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from copilot_core.asr import AsrProviderConfig, DeepgramProvider, SpeechSegment
from copilot_core.asr.provider import AsrError


class FakeSocket:
    def __init__(self, messages: list[dict[str, Any]], *, hang: bool = False) -> None:
        self._messages = list(messages)
        self.sent: list[Any] = []
        self.closed = False
        self.hang = hang

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if self.hang:
            await asyncio.sleep(10)
        if not self._messages:
            raise RuntimeError("connection closed")
        return json.dumps(self._messages.pop(0))

    async def close(self) -> None:
        self.closed = True


def segment(segment_id: str = "seg_1", source: str = "loopback") -> SpeechSegment:
    return SpeechSegment(
        segment_id=segment_id,
        source=source,
        start_ts_ns=1_000_000_000,
        end_ts_ns=2_000_000_000,
        frames=50,
        pcm16=b"\x00" * 640,
        sample_rate=16000,
        channels=1,
        peak_dbfs=-12.0,
        rms_dbfs=-20.0,
    )


def message(text: str, *, is_final: bool = False, speech_final: bool = False) -> dict[str, Any]:
    return {
        "is_final": is_final,
        "speech_final": speech_final,
        "channel": {"alternatives": [{"transcript": text, "confidence": 0.9}]},
    }


def make_provider(socket: FakeSocket, **overrides: Any) -> DeepgramProvider:
    async def connect(*_args: Any, **_kwargs: Any) -> FakeSocket:
        return socket

    config = AsrProviderConfig(
        name="deepgram",
        api_key_env="ASR_TEST_KEY",
        **overrides,
    )
    return DeepgramProvider(config, connect=connect)


async def collect(provider: DeepgramProvider, item: SpeechSegment) -> list[Any]:
    return [result async for result in provider.transcribe(item)]


async def test_partial_then_final() -> None:
    socket = FakeSocket([message("你", is_final=False), message("你好", is_final=True, speech_final=True)])
    provider = make_provider(socket)

    results = await collect(provider, segment())

    assert [result.text for result in results] == ["你", "你好"]
    assert [result.final for result in results] == [False, True]
    assert [result.revision for result in results] == [1, 2]
    assert socket.closed


async def test_multiple_partials_then_final() -> None:
    socket = FakeSocket(
        [
            message("你", is_final=False),
            message("你好", is_final=False),
            message("你好世界", is_final=True, speech_final=True),
        ]
    )
    results = await collect(make_provider(socket), segment())

    assert [result.revision for result in results] == [1, 2, 3]
    assert results[-1].text == "你好世界"
    assert results[-1].final


async def test_multiple_is_final_chunks_accumulate() -> None:
    socket = FakeSocket(
        [
            message("你好", is_final=True),
            message("世界", is_final=True),
            message("", is_final=True, speech_final=True),
        ]
    )
    results = await collect(make_provider(socket), segment())

    assert results[-1].text == "你好世界"
    assert results[-1].final
    assert all(not item.final for item in results[:-1])


async def test_speech_final_without_text_uses_committed() -> None:
    socket = FakeSocket(
        [
            message("你好", is_final=True),
            message("", is_final=False, speech_final=True),
        ]
    )
    results = await collect(make_provider(socket), segment())

    assert results[-1].text == "你好"
    assert results[-1].final


async def test_two_utterances_only_first_final() -> None:
    socket = FakeSocket(
        [
            message("第一句", is_final=True, speech_final=True),
            message("第二句", is_final=True, speech_final=True),
        ]
    )
    results = await collect(make_provider(socket), segment())

    finals = [item for item in results if item.final]
    assert len(finals) == 1
    assert finals[0].text == "第一句"


async def test_delayed_result_after_close_stream() -> None:
    socket = FakeSocket([message("延迟", is_final=True, speech_final=True)])
    results = await collect(make_provider(socket), segment())

    assert socket.sent[-1] == json.dumps({"type": "CloseStream"})
    assert results[-1].final


async def test_early_close_raises_connection_lost() -> None:
    socket = FakeSocket([])
    with pytest.raises(AsrError) as error:
        await collect(make_provider(socket), segment())
    assert error.value.code == "asr_connection_lost"
    assert socket.closed


async def test_receive_timeout_maps_to_error() -> None:
    socket = FakeSocket([], hang=True)
    provider = make_provider(socket, receive_timeout_seconds=0.05)
    with pytest.raises(AsrError) as error:
        await collect(provider, segment())
    assert error.value.code == "asr_timeout"


async def test_cancel_closes_active_socket() -> None:
    socket = FakeSocket([], hang=True)
    provider = make_provider(socket)
    task = asyncio.create_task(collect(provider, segment()))
    await asyncio.sleep(0.05)

    await provider.cancel()
    await asyncio.sleep(0.05)
    assert socket.closed or socket.hang
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_revision_is_per_segment() -> None:
    first = FakeSocket([message("甲", is_final=True, speech_final=True)])
    second = FakeSocket([message("乙", is_final=True, speech_final=True)])

    results_first = await collect(make_provider(first), segment("seg_a"))
    results_second = await collect(make_provider(second), segment("seg_b"))

    assert results_first[-1].revision == 1
    assert results_second[-1].revision == 1


async def test_empty_pcm_is_rejected() -> None:
    empty = SpeechSegment(
        segment_id="seg_x",
        source="loopback",
        start_ts_ns=0,
        end_ts_ns=1,
        frames=0,
        pcm16=b"",
        sample_rate=16000,
        channels=1,
        peak_dbfs=-120,
        rms_dbfs=-120,
    )
    with pytest.raises(AsrError) as error:
        await collect(make_provider(FakeSocket([])), empty)
    assert error.value.code == "asr_invalid_audio"
