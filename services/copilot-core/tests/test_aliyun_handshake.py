"""P3.2.10：Aliyun 握手验证的失败路径。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from copilot_core.asr import AsrAuthError, AsrProviderConfig, DashScopeParaformerProvider
from copilot_core.asr.provider import AsrError


class FakeSocket:
    def __init__(self, events: list[Any], *, hold: bool = False) -> None:
        self._events = list(events)
        self.hold = hold
        self.closed = False
        self.sent: list[Any] = []
        self.task_id: str | None = None

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if self._events:
            item = self._events.pop(0)
            return item if isinstance(item, str) else json.dumps(item)
        if self.hold:
            await asyncio.sleep(5)
        raise RuntimeError("socket closed")

    async def close(self) -> None:
        self.closed = True


def provider(socket: FakeSocket, **overrides: Any) -> DashScopeParaformerProvider:
    async def connect(*_args: Any, **_kwargs: Any) -> FakeSocket:
        return socket

    values: dict[str, Any] = {"name": "aliyun", "workspace_id": "ws-test", "api_key_env": "ASR_TEST_KEY"}
    values.update(overrides)
    return DashScopeParaformerProvider(AsrProviderConfig(**values), connect=connect)


async def test_normal_task_finished_sets_ready() -> None:
    socket = FakeSocket(
        [
            {"header": {"event": "task-started"}},
            {"header": {"event": "task-finished"}},
        ]
    )
    instance = provider(socket)

    await instance.connect("ticket")

    assert instance.status() == "ready"
    assert socket.closed
    assert instance.metrics()["active_web_sockets"] == 0


async def test_task_failed_sets_error_and_preserves_code() -> None:
    socket = FakeSocket(
        [
            {"header": {"event": "task-started"}},
            {
                "header": {
                    "event": "task-failed",
                    "error_code": "InvalidParameter",
                    "error_message": "bad sample rate",
                }
            },
        ]
    )
    instance = provider(socket)

    with pytest.raises(AsrError) as error:
        await instance.connect("ticket")

    assert error.value.code == "asr_provider_error"
    assert "InvalidParameter" in str(error.value)
    assert "bad sample rate" in str(error.value)
    assert instance.status() == "error"
    assert socket.closed
    assert instance.metrics()["active_web_sockets"] == 0


async def test_task_failed_auth_maps_to_auth_error() -> None:
    socket = FakeSocket(
        [
            {"header": {"event": "task-started"}},
            {"header": {"event": "task-failed", "error_code": "Unauthorized", "error_message": "bad key"}},
        ]
    )
    instance = provider(socket)

    with pytest.raises(AsrAuthError):
        await instance.connect("ticket")
    assert instance.status() == "error"
    assert socket.closed


async def test_receive_timeout_is_asr_timeout() -> None:
    socket = FakeSocket(
        [{"header": {"event": "task-started"}}],
        hold=True,
    )
    instance = provider(socket, receive_timeout_seconds=0.05)

    with pytest.raises(AsrError) as error:
        await instance.connect("ticket")

    assert error.value.code == "asr_timeout"
    assert instance.status() == "error"
    assert socket.closed
    assert instance.metrics()["active_web_sockets"] == 0


async def test_socket_closed_is_connection_lost() -> None:
    socket = FakeSocket([{"header": {"event": "task-started"}}])
    instance = provider(socket)

    with pytest.raises(AsrError) as error:
        await instance.connect("ticket")

    assert error.value.code == "asr_connection_lost"
    assert instance.status() == "error"
    assert socket.closed
    assert instance.metrics()["active_web_sockets"] == 0


async def test_unknown_event_is_protocol_error() -> None:
    socket = FakeSocket(
        [
            {"header": {"event": "task-started"}},
            {"header": {"event": "task-paused"}},
        ]
    )
    instance = provider(socket)

    with pytest.raises(AsrError) as error:
        await instance.connect("ticket")

    assert error.value.code == "asr_provider_error"
    assert "protocol error" in str(error.value)
    assert instance.status() == "error"
    assert socket.closed
    assert instance.metrics()["active_web_sockets"] == 0


async def test_malformed_message_is_protocol_error() -> None:
    socket = FakeSocket([{"header": {"event": "task-started"}}, "not-json"])
    instance = provider(socket)

    with pytest.raises(AsrError) as error:
        await instance.connect("ticket")

    assert error.value.code == "asr_provider_error"
    assert instance.status() == "error"
    assert socket.closed


async def test_cancelled_connect_closes_socket_and_never_ready() -> None:
    socket = FakeSocket([{"header": {"event": "task-started"}}], hold=True)
    instance = provider(socket)

    task = asyncio.create_task(instance.connect("ticket"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert socket.closed
    assert instance.status() != "ready"
    assert instance.metrics()["active_web_sockets"] == 0
