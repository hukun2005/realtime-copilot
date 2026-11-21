"""P2-C.2：spawn 与 Session 生命周期加固测试。

覆盖"进程创建期间"的竞态、会话任务隔离、shutdown 清理，以及 disconnect 与
stop / 进程退出同时发生的情况。断言真实副作用而非仅状态字符串。
"""

from __future__ import annotations

import asyncio

import pytest
from capture_harness import FakeConnection, Harness, wait_until
from copilot_core.capture import CaptureState


async def capturing(harness: Harness) -> FakeConnection:
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    await harness.controller.attach(connection)
    await wait_until(lambda: "capture.start" in connection.control_types)
    await harness.controller.handle_status(harness.status("capturing"), connection)
    assert harness.controller.state is CaptureState.CAPTURING
    return connection


async def test_stop_during_spawn_terminates_new_process(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    harness.spawn_gate = asyncio.Event()

    start_task = asyncio.create_task(harness.controller.start(harness.start_event()))
    await wait_until(lambda: harness.controller.state is CaptureState.STARTING)

    stop_task = asyncio.create_task(harness.controller.stop(harness.stop_event()))
    await wait_until(lambda: harness.controller.state is CaptureState.IDLE)
    await asyncio.gather(stop_task)

    # 放行 spawn：此时会话已关闭，进程必须被立即终止。
    harness.spawn_gate.set()
    await start_task

    assert len(harness.spawned) == 1
    assert harness.spawned[0].terminate_calls == 1
    assert harness.controller.state is CaptureState.IDLE
    assert not harness.pending_tasks()
    assert not harness.tickets.consume(harness.ticket()).ok


async def test_shutdown_during_spawn_terminates_new_process(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    harness.spawn_gate = asyncio.Event()

    start_task = asyncio.create_task(harness.controller.start(harness.start_event()))
    await wait_until(lambda: harness.controller.state is CaptureState.STARTING)

    await harness.controller.shutdown()
    harness.spawn_gate.set()
    await start_task

    assert harness.spawned[0].terminate_calls == 1
    assert harness.controller.state is CaptureState.IDLE
    assert not harness.pending_tasks()


async def test_spawn_failure_cleans_session_and_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)

    async def failing_exec(*_argv: str, **_kwargs: object) -> None:
        raise OSError("no such executable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec)

    await harness.controller.start(harness.start_event())

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "sidecar_unavailable"
    assert harness.controller.snapshot()["sidecarPid"] is None
    assert not harness.tickets.required


async def test_old_session_tasks_do_not_affect_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await capturing(harness)
    old_session = harness.controller._session
    assert old_session is not None
    old_tasks = [task for task in (old_session.timeout_task, old_session.watch_task) if task is not None]

    await harness.controller.handle_disconnect(connection)
    await wait_until(lambda: harness.controller.state is CaptureState.ERROR)

    await harness.controller.start(harness.start_event("req_" + "b" * 32))
    new_session = harness.controller._session
    assert new_session is not None
    assert new_session is not old_session
    assert harness.controller.snapshot()["generation"] == 2

    # 旧会话的任务已结束；新会话的任务仍然存活。
    await wait_until(lambda: all(task.done() for task in old_tasks))
    assert new_session.watch_task is not None and not new_session.watch_task.done()
    assert new_session.timeout_task is not None and not new_session.timeout_task.done()


async def test_shutdown_cancels_all_session_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    await capturing(harness)
    session = harness.controller._session
    assert session is not None
    tasks = [task for task in (session.timeout_task, session.watch_task, session.control_task) if task is not None]

    await harness.controller.shutdown()

    await wait_until(lambda: all(task.done() or task.cancelled() for task in tasks))
    assert harness.controller.state is CaptureState.IDLE
    assert harness.spawned[0].returncode is not None
    assert not harness.pending_tasks()


async def test_disconnect_and_stop_together_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await capturing(harness)

    stop_task = asyncio.create_task(harness.controller.stop(harness.stop_event()))
    await wait_until(lambda: harness.controller.state is CaptureState.STOPPING)

    await asyncio.gather(harness.controller.handle_disconnect(connection), stop_task)

    assert harness.controller.state is CaptureState.IDLE
    assert harness.controller.snapshot()["errorCode"] is None
    assert harness.spawned[0].terminate_calls <= 1
    assert not harness.pending_tasks()


async def test_disconnect_and_process_exit_together(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await capturing(harness)

    harness.spawned[0].exit_with(1)
    await asyncio.gather(
        harness.controller.handle_disconnect(connection),
        return_exceptions=True,
    )
    await wait_until(lambda: not harness.pending_tasks())

    assert harness.controller.state in {CaptureState.ERROR, CaptureState.IDLE}
    assert harness.spawned[0].terminate_calls <= 1
    assert harness.controller.snapshot()["sidecarPid"] is None


async def test_stop_during_spawn_does_not_double_terminate(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    harness.spawn_gate = asyncio.Event()

    start_task = asyncio.create_task(harness.controller.start(harness.start_event()))
    await wait_until(lambda: harness.controller.state is CaptureState.STARTING)

    stop_one = asyncio.create_task(harness.controller.stop(harness.stop_event()))
    stop_two = asyncio.create_task(harness.controller.stop(harness.stop_event("req_" + "c" * 32)))
    await asyncio.gather(stop_one, stop_two)

    harness.spawn_gate.set()
    await start_task

    assert harness.spawned[0].terminate_calls == 1
    assert harness.controller.state is CaptureState.IDLE
