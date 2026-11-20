"""控制面加固测试：并发、迟到状态与失败清理。

这些测试断言真实副作用：spawn 次数、进程终止次数、票据是否失效、后台任务是否结束、
连接引用是否清空，而不只是状态字符串。
"""

from __future__ import annotations

import asyncio

import pytest
from capture_harness import FakeConnection, Harness, wait_until
from copilot_core.capture import CaptureState


async def reach_capturing(harness: Harness) -> FakeConnection:
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    await harness.controller.attach(connection)
    await wait_until(lambda: "capture.start" in connection.control_types)
    await harness.controller.handle_status(harness.status("capturing"), connection)
    assert harness.controller.state is CaptureState.CAPTURING
    return connection


# --------------------------------------------------------------------- 并发


async def test_twenty_concurrent_starts_create_one_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)

    await asyncio.gather(*(harness.controller.start(harness.start_event()) for _ in range(20)))

    assert len(harness.spawned) == 1
    assert harness.controller.state is CaptureState.STARTING


async def test_twenty_concurrent_stops_terminate_once(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await reach_capturing(harness)

    tasks = [
        asyncio.create_task(harness.controller.stop(harness.stop_event()))
        for _ in range(20)
    ]
    await wait_until(lambda: "capture.stop" in connection.control_types)
    await harness.controller.handle_status(harness.status("idle"), connection)
    await asyncio.gather(*tasks)

    assert harness.controller.state is CaptureState.IDLE
    assert harness.spawned[0].terminate_calls == 1
    assert connection.control_types.count("capture.stop") == 1


async def test_start_and_stop_interleaved_is_consistent(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)

    results = await asyncio.gather(
        harness.controller.start(harness.start_event()),
        harness.controller.stop(harness.stop_event()),
        harness.controller.start(harness.start_event("req_" + "b" * 32)),
        harness.controller.stop(harness.stop_event("req_" + "c" * 32)),
        return_exceptions=True,
    )

    assert all(not isinstance(result, BaseException) for result in results)
    assert harness.controller.state in {
        CaptureState.STARTING,
        CaptureState.STOPPING,
        CaptureState.IDLE,
        CaptureState.CAPTURING,
    }
    alive = [process for process in harness.spawned if process.returncode is None]
    assert len(alive) <= 1


async def test_two_clients_start_one_session(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)

    await asyncio.gather(
        harness.controller.start(harness.start_event("req_" + "1" * 32)),
        harness.controller.start(harness.start_event("req_" + "2" * 32)),
    )

    assert len(harness.spawned) == 1


async def test_duplicate_start_while_connecting_does_not_spawn_again(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()

    await harness.controller.start(harness.start_event())
    await harness.controller.attach(connection)
    await harness.controller.start(harness.start_event("req_" + "d" * 32))

    assert len(harness.spawned) == 1
    assert harness.controller.state is CaptureState.STARTING


# --------------------------------------------------------------------- 迟到事件


async def test_idle_while_starting_fails_and_cleans(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    await harness.controller.attach(connection)

    await harness.controller.handle_status(harness.status("idle"), connection)
    await wait_until(lambda: not harness.pending_tasks())

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "sidecar_idle"
    assert harness.spawned[0].terminate_calls == 1
    assert not harness.tickets.consume(harness.ticket()).ok


async def test_capturing_after_stopping_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await reach_capturing(harness)

    stop_task = asyncio.create_task(harness.controller.stop(harness.stop_event()))
    await wait_until(lambda: harness.controller.state is CaptureState.STOPPING)
    await harness.controller.handle_status(harness.status("capturing"), connection)
    assert harness.controller.state is CaptureState.STOPPING

    await harness.controller.handle_status(harness.status("idle"), connection)
    await stop_task
    assert harness.controller.state is CaptureState.IDLE


async def test_idle_after_stopping_returns_to_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await reach_capturing(harness)

    stop_task = asyncio.create_task(harness.controller.stop(harness.stop_event()))
    await wait_until(lambda: harness.controller.state is CaptureState.STOPPING)
    await harness.controller.handle_status(harness.status("idle"), connection)
    await stop_task

    assert harness.controller.state is CaptureState.IDLE
    assert harness.spawned[0].terminate_calls == 1


async def test_error_state_is_not_cleared_by_late_events(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await reach_capturing(harness)

    await harness.controller.handle_status(
        harness.status("error", errorCode="device_lost", error="device disappeared"), connection
    )
    assert harness.controller.state is CaptureState.ERROR

    await harness.controller.handle_status(harness.status("capturing"), connection)
    await harness.controller.handle_status(harness.status("idle"), connection)

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "device_lost"


async def test_status_from_old_connection_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    old = await reach_capturing(harness)
    await harness.controller.handle_disconnect(old)
    await wait_until(lambda: harness.controller.state is CaptureState.ERROR)

    # 新会话
    await harness.controller.start(harness.start_event())
    new = FakeConnection()
    await harness.controller.attach(new)
    await wait_until(lambda: "capture.start" in new.control_types)

    await harness.controller.handle_status(harness.status("capturing"), old)
    assert harness.controller.state is CaptureState.STARTING

    await harness.controller.handle_status(harness.status("capturing"), new)
    assert harness.controller.state is CaptureState.CAPTURING


async def test_status_with_wrong_request_id_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = FakeConnection()
    await harness.controller.start(harness.start_event())
    await harness.controller.attach(connection)

    stale = harness.status("capturing")
    stale.request_id = "req_" + "9" * 32  # type: ignore[assignment]
    await harness.controller.handle_status(stale, connection)
    assert harness.controller.state is CaptureState.STARTING

    await harness.controller.handle_status(harness.status("capturing"), connection)
    assert harness.controller.state is CaptureState.CAPTURING


# --------------------------------------------------------------------- 清理


async def test_start_timeout_cleans_process_ticket_and_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch, start_timeout_ms=50)
    await harness.controller.start(harness.start_event())

    await wait_until(lambda: harness.controller.state is CaptureState.ERROR, seconds=2)
    await wait_until(lambda: not harness.pending_tasks())

    assert harness.controller.snapshot()["errorCode"] == "capture_timeout"
    assert harness.spawned[0].terminate_calls == 1
    assert not harness.tickets.consume(harness.ticket()).ok
    assert harness.controller.snapshot()["sidecarPid"] is None


async def test_stop_timeout_cleans_process_and_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch, stop_timeout_ms=50)
    await reach_capturing(harness)

    await harness.controller.stop(harness.stop_event())

    assert harness.controller.state is CaptureState.ERROR
    assert harness.controller.snapshot()["errorCode"] == "capture_timeout"
    assert harness.spawned[0].terminate_calls == 1
    assert not harness.tickets.consume(harness.ticket()).ok


async def test_sidecar_error_revokes_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await reach_capturing(harness)

    await harness.controller.handle_status(
        harness.status("error", errorCode="capture_failed", error="boom"), connection
    )

    assert harness.controller.state is CaptureState.ERROR
    assert not harness.tickets.consume(harness.ticket()).ok
    assert harness.spawned[0].terminate_calls == 1


async def test_shutdown_terminates_process_and_revokes_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await reach_capturing(harness)

    await harness.controller.shutdown()

    assert harness.controller.state is CaptureState.IDLE
    assert harness.spawned[0].returncode is not None
    assert not harness.tickets.consume(harness.ticket()).ok
    assert harness.controller.snapshot()["sidecarPid"] is None
    assert "capture.stop" in connection.control_types


async def test_disconnect_terminates_process(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    connection = await reach_capturing(harness)

    await harness.controller.handle_disconnect(connection)
    await wait_until(lambda: not harness.pending_tasks())

    assert harness.controller.state is CaptureState.ERROR
    assert harness.spawned[0].terminate_calls == 1
    assert harness.controller.snapshot()["errorCode"] == "audio_disconnected"


async def test_crash_and_user_stop_together_terminate_once(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    await reach_capturing(harness)

    stop_task = asyncio.create_task(harness.controller.stop(harness.stop_event()))
    await wait_until(lambda: harness.controller.state is CaptureState.STOPPING)
    harness.spawned[0].exit_with(1)

    await asyncio.gather(stop_task, return_exceptions=True)

    assert harness.controller.state in {CaptureState.IDLE, CaptureState.ERROR}
    assert harness.spawned[0].terminate_calls <= 1
    assert not harness.pending_tasks()


async def test_concurrent_start_does_not_leak_previous_process(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness(monkeypatch)
    await reach_capturing(harness)

    stop_task = asyncio.create_task(harness.controller.stop(harness.stop_event()))
    await wait_until(lambda: harness.controller.state is CaptureState.STOPPING)
    await harness.controller.handle_status(harness.status("idle"), harness.controller._session.connection)
    await stop_task

    await harness.controller.start(harness.start_event("req_" + "e" * 32))
    assert len(harness.spawned) == 2
    assert harness.controller.state is CaptureState.STARTING
    assert harness.controller.snapshot()["generation"] == 2
