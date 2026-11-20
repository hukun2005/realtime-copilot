import asyncio
import json

import pytest
from capture_harness import REQUEST_ID, FakeConnection, Harness
from copilot_core.capture import CaptureState

from copilot_protocol import make_event


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

    await harness.controller.attach(connection)
    await asyncio.sleep(0)
    assert connection.control[0][0] == "capture.start"

    await harness.controller.handle_status(harness.status("capturing"))
    assert harness.controller.state is CaptureState.CAPTURING
    assert harness.last_state() == "capturing"


async def test_duplicate_start_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    await harness.controller.attach(connection)
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
    await harness.controller.attach(connection)
    await harness.controller.handle_status(harness.status("capturing"))

    await harness.controller.stop(make_event("capture.stop", {}, REQUEST_ID))

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "capture_timeout"


async def test_sidecar_exit_enters_error(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    await harness.controller.attach(connection)
    await harness.controller.handle_status(harness.status("capturing"))

    harness.spawned[0].exit_with(1)
    await asyncio.sleep(0.05)

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "sidecar_exited"


async def test_sidecar_disconnect_enters_error(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    await harness.controller.attach(connection)
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
    await harness.controller.attach(connection)
    await harness.controller.handle_status(harness.status("capturing"))

    stop_task = asyncio.create_task(harness.controller.stop(make_event("capture.stop", {}, REQUEST_ID)))
    await asyncio.sleep(0.02)
    assert harness.controller.state is CaptureState.STOPPING
    assert connection.control[-1][0] == "capture.stop"

    await harness.controller.handle_status(harness.status("idle"), connection)
    await stop_task
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
