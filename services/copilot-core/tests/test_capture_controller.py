"""采集控制面：幂等、超时、异常退出与断连。

这些测试在进程边界用可控的假进程（真实跨进程链路由 Windows E2E 覆盖）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from copilot_core.capture import CaptureController, CaptureState
from copilot_core.config import CaptureConfig
from copilot_core.tickets import TicketIssuer

from copilot_protocol import make_event

REQUEST_ID = "req_" + "a" * 32


class FakeProcess:
    """最小的子进程替身：可等待退出、可终止。"""

    def __init__(self) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self._exit = asyncio.Event()

    async def wait(self) -> int:
        await self._exit.wait()
        return self.returncode or 0

    def terminate(self) -> None:
        self.returncode = -15
        self._exit.set()

    def kill(self) -> None:
        self.returncode = -9
        self._exit.set()

    def exit_with(self, code: int) -> None:
        self.returncode = code
        self._exit.set()


class FakeConnection:
    def __init__(self) -> None:
        self.control: list[tuple[str, dict[str, Any], str | None]] = []
        self.events: list[Any] = []

    async def send_control(self, event_type: str, payload: dict[str, Any], request_id: str | None = None) -> None:
        self.control.append((event_type, payload, request_id))

    async def send_event(self, event: Any) -> None:
        self.events.append(event)


class Harness:
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

        self.controller = CaptureController(
            config=config,
            tickets=TicketIssuer(),
            logger=__import__("logging").getLogger("test.capture"),
            broadcast=self._broadcast,
        )

    async def _broadcast(self, event: Any) -> None:
        self.events.append(event)

    def start_event(self) -> Any:
        return make_event(
            "capture.start",
            {"loopback": {"deviceId": None, "processId": None}, "mic": {"enabled": False}},
            REQUEST_ID,
        )

    def status(self, state: str, **extra: Any) -> Any:
        payload = {"state": state, "since": 1, **extra}
        return make_event("audio.status", payload)

    def last_state(self) -> str:
        return self.events[-1].payload.state


async def test_start_without_sidecar_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    harness.controller._config.sidecar_command = []

    await harness.controller.start(harness.start_event())

    assert harness.controller.state is CaptureState.IDLE
    assert harness.events[-1].type == "system.error"
    assert str(harness.events[-1].payload.code.root) == "sidecar_unavailable"
    assert not harness.spawned


async def test_start_reaches_capturing(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()

    await harness.controller.start(harness.start_event())
    assert harness.controller.state is CaptureState.STARTING
    assert harness.envs[0]["RTC_CAPTURE_CONTROL"] == "1"
    assert harness.envs[0]["RTC_AUDIO_TICKET"]

    harness.controller.attach(connection)
    await asyncio.sleep(0)
    assert connection.control[0][0] == "capture.start"

    await harness.controller.handle_status(harness.status("capturing"))
    assert harness.controller.state is CaptureState.CAPTURING
    assert harness.last_state() == "capturing"


async def test_duplicate_start_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    harness.controller.attach(connection)
    await harness.controller.handle_status(harness.status("capturing"))

    await harness.controller.start(harness.start_event())

    assert len(harness.spawned) == 1
    assert harness.controller.state is CaptureState.CAPTURING


async def test_start_timeout_enters_error(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch, start_timeout_ms=50)
    await harness.controller.start(harness.start_event())

    await asyncio.sleep(0.15)

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "capture_timeout"
    assert harness.spawned[0].returncode is not None


async def test_stop_timeout_enters_error(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch, stop_timeout_ms=50)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    harness.controller.attach(connection)
    await harness.controller.handle_status(harness.status("capturing"))

    await harness.controller.stop(make_event("capture.stop", {}, REQUEST_ID))
    await asyncio.sleep(0.15)

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "capture_timeout"


async def test_sidecar_exit_enters_error(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    harness.controller.attach(connection)
    await harness.controller.handle_status(harness.status("capturing"))

    harness.spawned[0].exit_with(1)
    await asyncio.sleep(0.05)

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "sidecar_exited"


async def test_sidecar_disconnect_enters_error(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    harness.controller.attach(connection)
    await harness.controller.handle_status(harness.status("capturing"))

    await harness.controller.handle_disconnect(connection)
    await asyncio.sleep(0.05)

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "audio_disconnected"


async def test_stop_from_idle_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)

    await harness.controller.stop(make_event("capture.stop", {}, REQUEST_ID))

    assert harness.controller.state is CaptureState.IDLE
    assert harness.last_state() == "idle"
    assert not harness.spawned


async def test_stop_returns_to_idle_after_sidecar_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    harness.controller.attach(connection)
    await harness.controller.handle_status(harness.status("capturing"))

    await harness.controller.stop(make_event("capture.stop", {}, REQUEST_ID))
    assert harness.controller.state is CaptureState.STOPPING
    assert connection.control[-1][0] == "capture.stop"

    await harness.controller.handle_status(harness.status("idle"))
    assert harness.controller.state is CaptureState.IDLE


async def test_error_state_can_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch, start_timeout_ms=50)
    await harness.controller.start(harness.start_event())
    await asyncio.sleep(0.15)
    assert harness.controller.state is CaptureState.ERROR

    harness.controller._config.start_timeout_ms = 5000
    await harness.controller.start(harness.start_event())

    assert harness.controller.state is CaptureState.STARTING
    assert len(harness.spawned) == 2


async def test_ticket_env_is_present_and_not_logged(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    harness = Harness(monkeypatch)
    await harness.controller.start(harness.start_event())
    ticket = harness.envs[0]["RTC_AUDIO_TICKET"]

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert ticket not in logged
    assert json.dumps({"ticket": ticket}) not in logged
