"""采集控制面：状态机 + sidecar 生命周期 + 超时 + 状态广播。

控制链路：

    UI/bridge --capture.start--> Core（本模块）--spawn--> audio sidecar
    sidecar --audio.status--> Core --broadcast--> UI

关键约束：

- 只有收到 ``capture.start`` 才会拉起 sidecar；Core 启动时永远是 idle，不做自动恢复。
- 每次启动都新签一张一次性音频票据，sidecar 在 hello 中出示。
- 启动/停止都有超时；超时进入 error 并结束进程。
- sidecar 异常退出或音频连接断开都进入 error，不会静默重连。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from copilot_protocol import make_audio_status, make_system_error
from copilot_protocol.models import AudioStatusEvent, CaptureStartEvent

from ..config import CaptureConfig
from ..tickets import TicketIssuer
from .state import CaptureState, CaptureStateMachine, InvalidTransition

#: sidecar 必须认的环境变量：控制模式下等待命令、携带票据。
ENV_CONTROL = "RTC_CAPTURE_CONTROL"
ENV_TICKET = "RTC_AUDIO_TICKET"


class CaptureUnavailable(Exception):
    """没有配置 sidecar 命令，控制面不可用。"""


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
        self._machine = CaptureStateMachine()
        self._process: asyncio.subprocess.Process | None = None
        self._connection: Any | None = None
        self._waiter: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._control_task: asyncio.Task[None] | None = None
        self._options: dict[str, Any] = {}
        self._request_id: str | None = None
        self._device_id: str | None = None
        self._mode: str | None = None

    # ------------------------------------------------------------------ 查询

    @property
    def state(self) -> CaptureState:
        """当前状态。"""
        return self._machine.state

    def snapshot(self) -> dict[str, Any]:
        """``/healthz`` 中的 ``capture`` 片段。"""
        machine = self._machine
        return {
            "state": machine.state.value,
            "since": machine.since_ms,
            "mode": self._mode,
            "deviceId": self._device_id,
            "requestId": self._request_id,
            "errorCode": machine.error_code,
            "error": machine.error_message,
            "sidecarPid": self._process.pid if self._process is not None else None,
            "sidecarCommand": list(self._config.sidecar_command),
        }

    # ------------------------------------------------------------------ 生命周期

    async def start(self, event: CaptureStartEvent) -> None:
        """处理 ``capture.start``；重复调用是幂等的，不会拉起第二个进程。"""
        if self._machine.state in (CaptureState.STARTING, CaptureState.CAPTURING):
            self._logger.info("capture.start ignored: already %s", self._machine.state.value)
            await self._broadcast(self._status_event())
            return

        if not self._config.sidecar_command:
            await self._reject(event, "sidecar_unavailable", "no sidecar command is configured")
            return

        if self._machine.state is CaptureState.ERROR:
            self._machine.transition(CaptureState.IDLE)
        self._machine.transition(CaptureState.STARTING)
        self._request_id = str(event.request_id.root) if event.request_id is not None else None
        self._options = event.payload.model_dump(by_alias=True, exclude_none=True)
        loopback = event.payload.loopback
        self._device_id = loopback.device_id if loopback is not None else None
        self._mode = "loopback"

        try:
            self._tickets.begin_session()
            ticket = self._tickets.issue()
            env = {
                **os.environ,
                ENV_CONTROL: "1",
                ENV_TICKET: ticket,
            }
            self._process = await asyncio.create_subprocess_exec(
                *self._config.sidecar_command,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (OSError, RuntimeError) as exc:
            self._tickets.end_session()
            self._fail("sidecar_unavailable", f"could not start audio sidecar: {exc}")
            await self._broadcast(self._status_event())
            return

        self._logger.info("capture starting: pid=%s", self._process.pid)
        self._arm(self._config.start_timeout_ms / 1000, self._on_start_timeout)
        self._watch_task = asyncio.create_task(self._watch_process(self._process))
        await self._broadcast(self._status_event())

    async def stop(self, event: Any) -> None:
        """处理 ``capture.stop``；重复调用是幂等的。"""
        state = self._machine.state
        if state in (CaptureState.IDLE, CaptureState.STOPPING):
            await self._broadcast(self._status_event())
            return

        if state is CaptureState.ERROR:
            self._machine.transition(CaptureState.IDLE)
            self._tickets.end_session()
            await self._broadcast(self._status_event())
            return

        self._request_id = str(event.request_id.root) if event.request_id is not None else None
        self._machine.transition(CaptureState.STOPPING)
        self._cancel_waiter()
        await self._broadcast(self._status_event())

        if self._connection is None:
            # 还没连上就要求停止：直接终止进程并回到 idle。
            await self._terminate_process()
            self._machine.transition(CaptureState.IDLE)
            self._tickets.end_session()
            await self._broadcast(self._status_event())
            return

        await self._send_control("capture.stop")
        self._arm(self._config.stop_timeout_ms / 1000, self._on_stop_timeout)

    async def shutdown(self) -> None:
        """Core 关闭：尽量优雅停止，随后强制结束。"""
        self._cancel_waiter()
        if self._process is None:
            self._tickets.end_session()
            return
        await self._send_control("capture.stop")
        try:
            await asyncio.wait_for(self._process.wait(), timeout=3)
        except TimeoutError:
            await self._terminate_process()
        self._process = None
        self._tickets.end_session()
        self._machine = CaptureStateMachine()

    # ------------------------------------------------------------------ 连接

    def attach(self, connection: Any) -> None:
        """sidecar 的音频连接已鉴权：把待发的 capture.start 交给它。"""
        self._connection = connection
        if self._machine.state is CaptureState.STARTING:
            self._control_task = asyncio.create_task(self._send_control("capture.start"))

    async def handle_disconnect(self, connection: Any) -> None:
        """音频连接断开。"""
        if connection is not self._connection:
            return
        self._connection = None
        if self._machine.state in (CaptureState.STARTING, CaptureState.CAPTURING):
            self._fail("audio_disconnected", "the audio sidecar connection closed unexpectedly")
            await self._terminate_process()
            await self._broadcast(self._status_event())
        elif self._machine.state is CaptureState.STOPPING:
            self._machine.transition(CaptureState.IDLE)
            self._tickets.end_session()
            await self._broadcast(self._status_event())

    async def handle_status(self, event: AudioStatusEvent) -> None:
        """处理 sidecar 上报的 ``audio.status``。"""
        reported = event.payload.state
        if reported == "capturing":
            if self._machine.state is CaptureState.STARTING:
                self._machine.transition(CaptureState.CAPTURING)
            if event.payload.device_id is not None:
                self._device_id = event.payload.device_id
            if event.payload.mode is not None:
                self._mode = event.payload.mode
            self._cancel_waiter()
            await self._broadcast(self._status_event())
            return

        if reported == "idle":
            if self._machine.state is CaptureState.STOPPING:
                self._machine.transition(CaptureState.IDLE)
                self._tickets.end_session()
                self._process = None
            elif self._machine.state is CaptureState.STARTING:
                self._fail("sidecar_idle", "the sidecar returned to idle before capturing")
            await self._broadcast(self._status_event())
            return

        if reported == "error":
            code = event.payload.error_code or "capture_failed"
            message = event.payload.error or "the sidecar reported an error"
            self._fail(code, message)
            await self._terminate_process()
            await self._broadcast(self._status_event())

    # ------------------------------------------------------------------ 内部

    async def _send_control(self, event_type: str) -> None:
        if self._connection is None:
            self._logger.warning("control command dropped: no audio connection (%s)", event_type)
            return
        payload = dict(self._options) if event_type == "capture.start" else {}
        try:
            await self._connection.send_control(event_type, payload, request_id=self._request_id)
        except Exception:
            self._logger.warning("failed to deliver %s to sidecar", event_type)

    async def _watch_process(self, process: asyncio.subprocess.Process) -> None:
        code = await process.wait()
        if self._process is not process:
            return
        self._process = None
        state = self._machine.state
        if state in (CaptureState.STARTING, CaptureState.CAPTURING):
            self._fail("sidecar_exited", f"the audio sidecar exited with code {code}")
            await self._broadcast(self._status_event())
        elif state is CaptureState.STOPPING:
            self._machine.transition(CaptureState.IDLE)
            self._tickets.end_session()
            await self._broadcast(self._status_event())

    def _arm(self, seconds: float, handler: Callable[[], Awaitable[None]]) -> None:
        self._cancel_waiter()
        self._waiter = asyncio.create_task(self._wait_then(seconds, handler))

    async def _wait_then(self, seconds: float, handler: Callable[[], Awaitable[None]]) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        await handler()

    def _cancel_waiter(self) -> None:
        if self._waiter is not None and not self._waiter.done():
            self._waiter.cancel()
        self._waiter = None

    async def _on_start_timeout(self) -> None:
        if self._machine.state is not CaptureState.STARTING:
            return
        self._fail("capture_timeout", "the sidecar did not start capturing in time")
        await self._terminate_process()
        await self._broadcast(self._status_event())

    async def _on_stop_timeout(self) -> None:
        if self._machine.state is not CaptureState.STOPPING:
            return
        self._fail("capture_timeout", "the sidecar did not stop in time")
        await self._terminate_process()
        await self._broadcast(self._status_event())

    async def _terminate_process(self) -> None:
        process = self._process
        if process is None:
            return
        self._process = None
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.kill()
                await process.wait()

    def _fail(self, code: str, message: str) -> None:
        previous = self._machine.state
        self._machine.fail(code, message)
        self._logger.warning(
            "capture failed: %s -> error (%s)", previous.value, code
        )

    def _reject(self, event: CaptureStartEvent, code: str, message: str) -> Awaitable[None]:
        async def send() -> None:
            await self._broadcast(
                make_system_error(
                    code=code,  # type: ignore[arg-type]
                    message=message,
                    retryable=False,
                    request_id=event.request_id,
                )
            )

        return send()

    def _status_event(self) -> AudioStatusEvent:
        machine = self._machine
        return make_audio_status(
            state=machine.state.value,
            since=machine.since_ms,
            source="loopback" if self._mode == "loopback" else self._mode,
            device_id=self._device_id,
            mode=self._mode,
            error=machine.error_message,
            error_code=machine.error_code,
        )


__all__ = ["CaptureController", "CaptureState", "CaptureUnavailable", "InvalidTransition"]
