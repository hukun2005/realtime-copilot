"""P3.3：Aliyun Paraformer 连接复用、来源隔离与生命周期。"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any

import pytest
from copilot_core.asr import AsrProviderConfig, DashScopeParaformerProvider, SpeechSegment
from copilot_core.asr.provider import AsrAuthError, AsrError


class ReusableFakeSocket:
    """按 run-task / finish-task 自动产生服务端事件。"""

    def __init__(
        self,
        *,
        fail_first_tasks: int = 0,
        fail_at_start: bool = False,
        hold: bool = False,
        hold_after_finish: bool = False,
    ) -> None:
        self.queue: deque[dict[str, Any]] = deque()
        self.sent: list[Any] = []
        self.task_ids: list[str] = []
        self.task_id: str | None = None
        self.closed = False
        self.hold = hold
        self.hold_after_finish = hold_after_finish
        self.fail_at_start = fail_at_start
        self.fail_remaining = fail_first_tasks
        self.audio_bytes = 0

    async def send(self, data: Any) -> None:
        self.sent.append(data)
        if isinstance(data, (bytes, bytearray)):
            self.audio_bytes += len(data)
            self.queue.append(_sentence("你好", end=False))
            return
        message = json.loads(data)
        action = (message.get("header") or {}).get("action")
        if action == "run-task":
            self.task_id = (message.get("header") or {}).get("task_id")
            self.task_ids.append(self.task_id or "")
            if self.fail_at_start and self.fail_remaining > 0:
                self.fail_remaining -= 1
                self.queue.append(
                    {"header": {"event": "task-failed", "error_code": "InternalError", "error_message": "boom"}}
                )
                return
            self.queue.append({"header": {"event": "task-started"}})
        elif action == "finish-task":
            if self.fail_remaining > 0:
                self.fail_remaining -= 1
                self.queue.append(
                    {"header": {"event": "task-failed", "error_code": "InternalError", "error_message": "boom"}}
                )
            elif self.hold_after_finish:
                return
            else:
                self.queue.append(_sentence("你好。", end=True))
                self.queue.append({"header": {"event": "task-finished"}})

    async def recv(self) -> str:
        if self.queue:
            return json.dumps(self.queue.popleft())
        if self.hold:
            await asyncio.sleep(5)
        raise RuntimeError("socket closed")

    async def close(self) -> None:
        self.closed = True


def _sentence(text: str, *, end: bool) -> dict[str, Any]:
    return {
        "header": {"event": "result-generated"},
        "payload": {"output": {"sentence": {"text": text, "sentence_end": end}}},
    }


def segment(index: int, source: str = "loopback") -> SpeechSegment:
    return SpeechSegment(
        segment_id=f"seg_{source}_{index}",
        source=source,
        start_ts_ns=index * 1_000_000,
        end_ts_ns=(index + 1) * 1_000_000,
        frames=50,
        pcm16=b"\x00" * 640,
        sample_rate=16000,
        channels=1,
        peak_dbfs=-20,
        rms_dbfs=-30,
    )


def make_provider(sockets: list[ReusableFakeSocket], **overrides: Any) -> DashScopeParaformerProvider:
    created: list[ReusableFakeSocket] = []

    async def connect(*_args: Any, **_kwargs: Any) -> ReusableFakeSocket:
        socket = sockets[min(len(created), len(sockets) - 1)]
        created.append(socket)
        return socket

    values: dict[str, Any] = {
        "name": "aliyun",
        "workspace_id": "ws-test",
        "api_key_env": "ASR_TEST_KEY",
        "reuse_connections": True,
    }
    values.update(overrides)
    return DashScopeParaformerProvider(AsrProviderConfig(**values), connect=connect)


async def collect(provider: DashScopeParaformerProvider, item: SpeechSegment) -> list[Any]:
    return [result async for result in provider.transcribe(item)]


async def test_100_segments_reuse_single_connection() -> None:
    socket = ReusableFakeSocket()
    provider = make_provider([socket])

    for index in range(100):
        results = await collect(provider, segment(index))
        assert results[-1].final

    metrics = provider.metrics()
    assert metrics["connections_created"] == 1
    assert metrics["connections_reused"] == 99
    assert metrics["tasks_completed"] == 100
    assert metrics["active_web_sockets"] == 1  # 复用连接保持打开
    await provider.close()
    assert provider.metrics()["active_web_sockets"] == 0
    assert len(socket.task_ids) == 100
    assert len(set(socket.task_ids)) == 100


async def test_task_finished_precedes_next_run_task() -> None:
    socket = ReusableFakeSocket()
    provider = make_provider([socket])

    for index in range(5):
        await collect(provider, segment(index))

    actions = [
        (json.loads(item).get("header") or {}).get("action")
        for item in socket.sent
        if isinstance(item, str)
    ]
    assert actions == ["run-task", "finish-task"] * 5


async def test_same_source_is_serialized() -> None:
    socket = ReusableFakeSocket()
    provider = make_provider([socket])

    async def run(index: int) -> None:
        await collect(provider, segment(index, "loopback"))

    await asyncio.gather(*(run(index) for index in range(10)))

    assert provider.metrics()["connections_created"] == 1
    assert len(socket.task_ids) == 10


async def test_two_sources_use_separate_connections() -> None:
    loop_socket = ReusableFakeSocket()
    mic_socket = ReusableFakeSocket()
    provider = make_provider([loop_socket, mic_socket])

    async def run(source: str) -> None:
        for index in range(5):
            await collect(provider, segment(index, source))

    await asyncio.gather(run("loopback"), run("mic"))

    metrics = provider.metrics()
    assert metrics["connections_created"] == 2
    assert len(loop_socket.task_ids) == 5
    assert len(mic_socket.task_ids) == 5


async def test_failure_discards_connection_and_retries_once() -> None:
    first = ReusableFakeSocket(fail_first_tasks=1, fail_at_start=True)
    second = ReusableFakeSocket()
    provider = make_provider([first, second])

    results = await collect(provider, segment(1))

    assert results[-1].final
    assert first.closed
    assert provider.metrics()["connections_created"] == 2
    assert provider.metrics()["tasks_completed"] == 1
    await provider.close()
    assert provider.metrics()["active_web_sockets"] == 0


async def test_failure_after_partial_does_not_retry() -> None:
    socket = ReusableFakeSocket(fail_first_tasks=1)
    provider = make_provider([socket])

    # 第一个任务失败前已产出 partial，不能重放结果。
    seen: list[Any] = []
    with pytest.raises(AsrError):
        async for result in provider.transcribe(segment(1)):
            seen.append(result)
    assert socket.closed


async def test_idle_timeout_rebuilds_connection() -> None:
    first = ReusableFakeSocket()
    second = ReusableFakeSocket()
    provider = make_provider([first, second], idle_timeout_seconds=0.01)

    await collect(provider, segment(1))
    await asyncio.sleep(0.05)
    await collect(provider, segment(2))

    metrics = provider.metrics()
    assert metrics["connections_created"] == 2
    assert metrics["idle_closes"] == 1
    assert first.closed


async def test_cancel_discards_session_and_closes_socket() -> None:
    socket = ReusableFakeSocket(hold=True, hold_after_finish=True)
    provider = make_provider([socket])
    task = asyncio.create_task(collect(provider, segment(1)))
    await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert socket.closed
    assert provider.metrics()["active_web_sockets"] == 0


async def test_close_is_idempotent() -> None:
    socket = ReusableFakeSocket()
    provider = make_provider([socket])
    await collect(provider, segment(1))

    await provider.close()
    await provider.close()

    assert socket.closed
    assert provider.metrics()["active_web_sockets"] == 0


async def test_legacy_mode_opens_connection_per_segment() -> None:
    sockets = [ReusableFakeSocket() for _ in range(3)]
    provider = make_provider(sockets, reuse_connections=False)

    for index in range(3):
        await collect(provider, segment(index))

    metrics = provider.metrics()
    assert metrics["connections_created"] == 0
    assert metrics["connection_attempts"] == 3


def test_reuse_requires_api_key_or_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASR_TEST_KEY", raising=False)
    config = AsrProviderConfig(name="aliyun", workspace_id="ws-test", api_key_env="ASR_TEST_KEY")
    with pytest.raises(AsrAuthError):
        DashScopeParaformerProvider(config)
