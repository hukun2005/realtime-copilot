"""采集控制面：状态机 + sidecar 生命周期 + 超时 + 状态广播。

控制链路：

    UI/bridge --capture.start--> Core（本模块）--spawn--> audio sidecar
    sidecar --audio.status--> Core --broadcast--> UI

并发与清理约定：

- 所有状态检查、迁移与会话登记都在同一把 :class:`asyncio.Lock` 下完成；
  **不在持锁期间等待进程退出或等待 sidecar 状态回报**。
- 每次 start 生成一个新的 ``_Session``（含 generation），状态回报必须匹配
  当前会话与连接实例，迟到消息一律忽略。
- 失败收尾只有一条路径 :meth:`CaptureController._fail_and_cleanup`：取消超时任务、
  撤销票据、终止进程、清空连接、广播错误，且对同一会话幂等。
- Core 启动永远是 idle；没有 capture.start 不会拉起任何采集进程。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from copilot_protocol import make_audio_status, make_system_error
from copilot_protocol.models import AudioStatusEvent, CaptureStartEvent

from ..config import CaptureConfig
from ..tickets import TicketIssuer
from .state import CaptureState, CaptureStateMachine, InvalidTransition

#: sidecar 必须认的环境变量：控制模式下等待命令、携带票据。
ENV_CONTROL = "RTC_CAPTURE_CONTROL"
ENV_TICKET = "RTC_AUDIO_TICKET"

#: 关闭时等待 sidecar 自行退出的上限（秒）。
SHUTDOWN_GRACE_SECONDS = 2.0


class CaptureUnavailable(Exception):
    """没有配置 sidecar 命令，控制面不可用。"""


@dataclass
class _Session:
    """一次采集会话的全部可变状态。"""

    generation: int
    request_id: str | None
    options: dict[str, Any]
    device_id: str | None
    mode: str | None
    process: asyncio.subprocess.Process | None = None
    connection: Any | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False
    terminating: bool = False


class CaptureController:
    """采集控制面。"""

    def __init__(
        self,
        *,
        config: CaptureConfig,
        tickets: TicketIssuer,
        logger: Any,
        broadcast: Callable[[Any], Awaitable[None]],
    ) -> None:
        self._config = config
        self._tickets = tickets
        self._logger = logger
        self._broadcast = broadcast
        self._lock = asyncio.Lock()
        self._machine = CaptureStateMachine()
        self._session: _Session | None = None
        self._generation = 0
        self._last_exit_code: int | None = None
        self._last_termination: str | None = None
        self._timeout_task: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._control_task: asyncio.Task[None] | None = None
        self._shutting_down = False

    # ------------------------------------------------------------------ 查询

    @property
    def state(self) -> CaptureState:
        """当前状态。"""
        return self._machine.state

    @property
    def generation(self) -> int:
        """当前会话代次；每次 start 递增。"""
        return self._generation

    def snapshot(self) -> dict[str, Any]:
        """``/healthz`` 中的 ``capture`` 片段。"""
        machine = self._machine
        session = self._session
        return {
            "state": machine.state.value,
            "since": machine.since_ms,
            "generation": self._generation,
            "mode": session.mode if session else None,
            "deviceId": session.device_id if session else None,
            "requestId": session.request_id if session else None,
            "errorCode": machine.error_code,
            "error": machine.error_message,
            "sidecarPid": session.process.pid if session and session.process else None,
            "sidecarExitCode": self._last_exit_code,
            "sidecarTermination": self._last_termination,
            "sidecarCommand": list(self._config.sidecar_command),
        }

    # ------------------------------------------------------------------ 生命周期

    async def start(self, event: CaptureStartEvent) -> None:
        """处理 ``capture.start``。

        幂等：已经在 starting/capturing 时只广播当前状态，不创建第二个进程。
        """
        emit: list[Any] = []
        session: _Session | None = None

        async with self._lock:
            if self._shutting_down:
                emit.append(self._error(event, "sidecar_unavailable", "core is shutting down"))
                emit.append(self._status_event())
            elif self._machine.state in (CaptureState.STARTING, CaptureState.CAPTURING):
                self._logger.info("capture.start ignored: already %s", self._machine.state.value)
                emit.append(self._status_event())
            elif not self._config.sidecar_command:
                emit.append(self._error(event, "sidecar_unavailable", "no sidecar command is configured"))
            else:
                session = await self._begin_start_locked(event, emit)

        await self._emit(emit)

        if session is None:
            return

        self._timeout_task = asyncio.create_task(self._start_timeout(session.generation))
        self._watch_task = asyncio.create_task(self._watch_process(session))
        await self._emit([self._status_event()])

    async def stop(self, event: Any) -> None:
        """处理 ``capture.stop``；重复调用幂等，且区分正常停止与失败。"""
        emit: list[Any] = []
        session: _Session | None = None
        need_terminate_immediately = False

        async with self._lock:
            state = self._machine.state
            if state in (CaptureState.IDLE, CaptureState.STOPPING):
                emit.append(self._status_event())
            elif state is CaptureState.ERROR:
                self._reset_to_idle_locked()
                emit.append(self._status_event())
            else:
                self._machine.transition(CaptureState.STOPPING)
                session = self._session
                emit.append(self._status_event())
                if session is not None and session.connection is None:
                    need_terminate_immediately = True
                self._cancel_timeout_locked()

        if session is None:
            await self._emit(emit)
            return

        if need_terminate_immediately:
            await self._emit(emit)
            await self._terminate(session)
            await self._conclude_stop(session, expected_generation=session.generation)
            return

        await self._emit(emit)
        if session.connection is not None:
            try:
                await session.connection.send_control("capture.stop", {}, session.request_id)
            except Exception:
                self._logger.warning("failed to deliver capture.stop to sidecar")

        try:
            await asyncio.wait_for(session.stopped.wait(), timeout=self._config.stop_timeout_ms / 1000)
        except TimeoutError:
            await self._fail_and_cleanup(
                session,
                "capture_timeout",
                "the sidecar did not stop in time",
            )
            return

        await self._conclude_stop(session, expected_generation=session.generation)

    async def shutdown(self) -> None:
        """Core 关闭：停止 sidecar 并等待退出，尽量不留残留进程。"""
        emit: list[Any] = []
        async with self._lock:
            self._shutting_down = True
            session = self._session
            self._session = None
            self._cancel_timeout_locked()
            self._cancel_watch_locked()
            if session is not None:
                session.closed = True
            self._tickets.end_session()
            self._machine = CaptureStateMachine()

        if session is not None:
            emit.append(self._status_event())
            if session.connection is not None:
                try:
                    await session.connection.send_control("capture.stop", {}, session.request_id)
                except Exception:
                    self._logger.debug("shutdown: capture.stop was not delivered")
            await self._terminate(session, grace=SHUTDOWN_GRACE_SECONDS)

        await self._emit(emit)

    # ------------------------------------------------------------------ 连接

    async def attach(self, connection: Any) -> None:
        """sidecar 的音频连接已鉴权：登记到当前会话并下发 capture.start。"""
        session: _Session | None = None
        async with self._lock:
            current = self._session
            if (
                current is not None
                and not current.closed
                and self._machine.state is CaptureState.STARTING
                and current.connection is None
            ):
                current.connection = connection
                session = current

        if session is not None:
            self._control_task = asyncio.create_task(self._send_start(session))

    async def _send_start(self, session: _Session) -> None:
        connection = session.connection
        if connection is None:
            return
        try:
            await connection.send_control("capture.start", session.options, session.request_id)
        except Exception:
            self._logger.warning("failed to deliver capture.start to sidecar")
            await self._fail_and_cleanup(session, "audio_disconnected", "could not send capture.start")

    async def handle_disconnect(self, connection: Any) -> None:
        """音频连接断开：异常断开进 error，停止过程中的断开按正常结束处理。"""
        session: _Session | None = None
        async with self._lock:
            current = self._session
            if current is None or current.connection is not connection:
                return
            current.connection = None
            session = current

        if self._machine.state is CaptureState.STOPPING:
            await self._conclude_stop(session, expected_generation=session.generation)
        elif self._machine.state in (CaptureState.STARTING, CaptureState.CAPTURING):
            await self._fail_and_cleanup(
                session,
                "audio_disconnected",
                "the audio sidecar connection closed unexpectedly",
            )

    async def handle_status(self, event: AudioStatusEvent, connection: Any | None = None) -> None:
        """处理 sidecar 上报的 ``audio.status``。

        迟到、跨连接、跨会话（generation/requestId 不匹配）的消息一律忽略；
        ERROR 状态不会被后续迟到消息清除。
        """
        session: _Session | None = None
        action: str | None = None
        error_code = "capture_failed"
        error_message = "the sidecar reported an error"

        async with self._lock:
            current = self._session
            if current is None or current.closed:
                return
            if connection is not None and current.connection is not connection:
                return
            reported_request = _as_text(event.request_id)
            if (
                reported_request is not None
                and current.request_id is not None
                and reported_request != current.request_id
            ):
                return

            reported = event.payload.state
            state = self._machine.state

            if reported == "capturing":
                if state is CaptureState.STARTING:
                    self._machine.transition(CaptureState.CAPTURING)
                    current.started.set()
                    action = "started"
            elif reported == "idle":
                if state is CaptureState.STOPPING:
                    current.stopped.set()
                    action = "stopped"
                elif state is CaptureState.STARTING:
                    action = "failed_idle"
                    session = current
            elif reported == "error":
                if state in (CaptureState.STARTING, CaptureState.CAPTURING, CaptureState.STOPPING):
                    action = "failed_status"
                    session = current
                    error_code = event.payload.error_code or "capture_failed"
                    error_message = event.payload.error or "the sidecar reported an error"
            else:
                return

            if action == "stopped":
                session = current

        if action == "stopped" and session is not None:
            await self._conclude_stop(session, expected_generation=session.generation)
        elif action == "failed_idle" and session is not None:
            await self._fail_and_cleanup(session, "sidecar_idle", "the sidecar returned to idle before capturing")
        elif action == "failed_status" and session is not None:
            await self._fail_and_cleanup(session, error_code, error_message)
        elif action == "started":
            await self._emit([self._status_event()])

    # ------------------------------------------------------------------ 内部

    async def _begin_start_locked(self, event: CaptureStartEvent, emit: list[Any]) -> _Session | None:
        if self._machine.state is CaptureState.ERROR:
            self._machine.transition(CaptureState.IDLE)

        loopback = event.payload.loopback
        options = event.payload.model_dump(by_alias=True, exclude_none=True)
        request_id = _as_text(event.request_id)

        self._machine.transition(CaptureState.STARTING)
        self._generation += 1
        session = _Session(
            generation=self._generation,
            request_id=request_id,
            options=options,
            device_id=loopback.device_id if loopback is not None else None,
            mode="loopback",
        )
        self._session = session

        try:
            self._tickets.begin_session()
            ticket = self._tickets.issue()
            env = {**os.environ, ENV_CONTROL: "1", ENV_TICKET: ticket}
            session.process = await asyncio.create_subprocess_exec(
                *self._config.sidecar_command,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (OSError, RuntimeError) as exc:
            self._tickets.end_session()
            self._machine.fail("sidecar_unavailable", f"could not start audio sidecar: {exc}")
            self._session = None
            session.closed = True
            emit.append(self._error(event, "sidecar_unavailable", "could not start the audio sidecar"))
            return None

        self._logger.info(
            "capture starting: pid=%s generation=%s", session.process.pid, session.generation
        )
        return session

    async def _start_timeout(self, generation: int) -> None:
        try:
            await asyncio.sleep(self._config.start_timeout_ms / 1000)
        except asyncio.CancelledError:
            return

        async with self._lock:
            session = self._session
            if (
                session is None
                or session.generation != generation
                or self._machine.state is not CaptureState.STARTING
            ):
                return
        await self._fail_and_cleanup(session, "capture_timeout", "the sidecar did not start capturing in time")

    async def _watch_process(self, session: _Session) -> None:
        process = session.process
        if process is None:
            return
        try:
            code = await process.wait()
        except asyncio.CancelledError:
            return
        self._last_exit_code = code
        self._last_termination = "self"

        async with self._lock:
            if self._session is not session or session.closed:
                return
            state = self._machine.state

        if state in (CaptureState.STARTING, CaptureState.CAPTURING):
            await self._fail_and_cleanup(
                session, "sidecar_exited", f"the audio sidecar exited with code {code}"
            )
        elif state is CaptureState.STOPPING:
            await self._conclude_stop(session, expected_generation=session.generation)

    async def _conclude_stop(self, session: _Session, *, expected_generation: int) -> None:
        """正常停止收尾：STOPPING -> IDLE，撤销票据并清理进程。"""
        emit: list[Any] = []
        async with self._lock:
            if (
                self._session is not session
                or session.generation != expected_generation
                or session.closed
                or self._machine.state is not CaptureState.STOPPING
            ):
                return
            session.closed = True
            session.stopped.set()
            self._machine.transition(CaptureState.IDLE)
            self._tickets.end_session()
            self._session = None
            self._cancel_timeout_locked()
            self._cancel_watch_locked()
            emit.append(self._status_event())

        await self._terminate(session)
        await self._emit(emit)

    async def _fail_and_cleanup(self, session: _Session, code: str, message: str) -> None:
        """统一失败收尾（幂等）：进入 ERROR、撤销票据、终止进程、广播。"""
        emit: list[Any] = []
        async with self._lock:
            if self._session is not session or session.closed:
                return
            session.closed = True
            session.stopped.set()
            self._session = None
            self._tickets.end_session()
            self._cancel_timeout_locked()
            self._cancel_watch_locked()
            self._machine.fail(code, message)
            emit.append(self._status_event())

        self._logger.warning("capture failed: %s (%s)", code, message)
        await self._terminate(session)
        await self._emit(emit)

    async def _terminate(self, session: _Session, *, grace: float = 2.0) -> None:
        """终止 sidecar 进程；对同一会话只执行一次实质终止。"""
        process = session.process
        if process is None or session.terminating:
            return
        session.terminating = True
        session.process = None

        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(process.wait(), timeout=grace)
            except TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    return
                await process.wait()
        self._last_exit_code = process.returncode
        self._last_termination = "core"

    def _reset_to_idle_locked(self) -> None:
        self._machine.transition(CaptureState.IDLE)
        self._tickets.end_session()
        self._session = None
        self._cancel_timeout_locked()
        self._cancel_watch_locked()

    def _cancel_timeout_locked(self) -> None:
        if self._timeout_task is not None and not self._timeout_task.done():
            self._timeout_task.cancel()
        self._timeout_task = None

    def _cancel_watch_locked(self) -> None:
        task = self._watch_task
        self._watch_task = None
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _emit(self, events: list[Any]) -> None:
        for event in events:
            await self._broadcast(event)

    def _status_event(self) -> Any:
        machine = self._machine
        session = self._session
        return make_audio_status(
            state=machine.state.value,
            since=machine.since_ms,
            source="loopback" if (session and session.mode == "loopback") else None,
            device_id=session.device_id if session else None,
            mode=session.mode if session else None,
            error=machine.error_message,
            error_code=machine.error_code,
        )

    def _error(self, event: Any, code: str, message: str) -> Any:
        return make_system_error(
            code=code,  # type: ignore[arg-type]
            message=message,
            retryable=False,
            request_id=event.request_id if event is not None else None,
        )


def _as_text(value: Any) -> str | None:
    """把 RootModel / str 的 ID 统一成字符串。"""
    if value is None:
        return None
    return str(getattr(value, "root", value))


__all__ = ["CaptureController", "CaptureState", "CaptureUnavailable", "InvalidTransition"]
