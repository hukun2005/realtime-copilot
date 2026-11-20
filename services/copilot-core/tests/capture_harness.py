"""控制面测试夹具：可控的假 sidecar 进程、连接与广播收集器。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from copilot_core.capture import CaptureController
from copilot_core.config import CaptureConfig
from copilot_core.tickets import TicketIssuer

from copilot_protocol import make_event

REQUEST_ID = "req_" + "a" * 32


class FakeProcess:
    """最小的子进程替身：可等待退出、可终止，并记录终止次数。"""

    def __init__(self) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self._exit = asyncio.Event()

    async def wait(self) -> int:
        await self._exit.wait()
        return self.returncode or 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.returncode is None:
            self.returncode = -15
        self._exit.set()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self._exit.set()

    def exit_with(self, code: int) -> None:
        self.returncode = code
        self._exit.set()


class FakeConnection:
    def __init__(self) -> None:
        self.control: list[tuple[str, dict[str, Any], str | None]] = []

    async def send_control(self, event_type: str, payload: dict[str, Any], request_id: str | None = None) -> None:
        self.control.append((event_type, payload, request_id))

    @property
    def control_types(self) -> list[str]:
        return [item[0] for item in self.control]


class Harness:
    """装配一个使用假进程的控制器，并记录所有副作用。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, **config_overrides: Any) -> None:
        self.spawned: list[FakeProcess] = []
        self.argv: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.events: list[Any] = []

        async def fake_exec(*argv: str, **kwargs: Any) -> FakeProcess:
            process = FakeProcess()
            self.spawned.append(process)
            self.argv.append(list(argv))
            self.envs.append(dict(kwargs.get("env") or {}))
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        config = CaptureConfig(sidecar_command=["dotnet", "sidecar.dll"])
        for key, value in config_overrides.items():
            object.__setattr__(config, key, value)

        self.tickets = TicketIssuer()
        self.controller = CaptureController(
            config=config,
            tickets=self.tickets,
            logger=logging.getLogger("test.capture"),
            broadcast=self._broadcast,
        )

    async def _broadcast(self, event: Any) -> None:
        self.events.append(event)

    def start_event(self, request_id: str = REQUEST_ID) -> Any:
        return make_event(
            "capture.start",
            {"loopback": {"deviceId": None, "processId": None}, "mic": {"enabled": False}},
            request_id,
        )

    def stop_event(self, request_id: str = REQUEST_ID) -> Any:
        return make_event("capture.stop", {}, request_id)

    def status(self, state: str, **extra: Any) -> Any:
        return make_event("audio.status", {"state": state, "since": 1, **extra})

    def last_state(self) -> str:
        return self.events[-1].payload.state

    def ticket(self, index: int = 0) -> str | None:
        return self.envs[index].get("RTC_AUDIO_TICKET") if len(self.envs) > index else None

    def pending_tasks(self) -> list[asyncio.Task[Any]]:
        return [
            task
            for task in (self.controller._timeout_task, self.controller._watch_task)
            if task is not None and not task.done()
        ]


async def wait_until(predicate, *, seconds: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not met in time")
