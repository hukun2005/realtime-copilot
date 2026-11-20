"""单条 WebSocket 连接：握手鉴权、心跳与事件分发。

生命周期（与 ``PLUGIN_API.md`` §2 一致）：

1. ``accept``，随后校验来源地址必须是回环。
2. 在 ``auth_timeout_ms`` 内等待首帧 ``hello``；超时、版本不兼容、令牌错误都会
   回一条 ``system.error`` 再关闭连接。
3. 鉴权通过后回 ``system.ready``，启动心跳任务。
4. 之后按事件类型分发；Phase 1 尚未实现的能力回 ``not_implemented``，不假装成功。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from copilot_protocol import (
    PROTOCOL_VERSION,
    AudioStatusEvent,
    CaptureStartEvent,
    CaptureStopEvent,
    ConcreteEvent,
    ErrorCode,
    HelloEvent,
    PingEvent,
    PongEvent,
    ProtocolViolation,
    dumps,
    make_ping,
    make_pong,
    make_system_error,
    make_system_ready,
    parse_event,
)

from ..config import AppConfig
from ..security import tokens_equal

#: 允许建立连接的来源地址。Core 只绑定回环，这里是纵深防御。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})

#: 收到即关闭连接的错误码（协议层违规）。
FATAL_CODES = frozenset({"protocol_version_unsupported", "invalid_envelope", "invalid_payload"})

#: 关闭码。1002=协议错误，1008=策略违规，1009=消息过大，1001=对端失联。
CLOSE_PROTOCOL = 1002
CLOSE_POLICY = 1008
CLOSE_TOO_LARGE = 1009
CLOSE_GONE = 1001
CLOSE_NORMAL = 1000


class EventConnection:
    """一条已接入的客户端连接。"""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        config: AppConfig,
        token: str,
        version: str,
        logger: Any,
        on_authenticated: Callable[[str], None] | None = None,
    ) -> None:
        """初始化连接对象。

        Args:
            websocket: Starlette WebSocket（尚未 accept）。
            config: 服务配置。
            token: 本次运行的鉴权令牌。
            version: 服务版本，写入 ``system.ready``。
            logger: 结构化 logger。
        """
        self._ws = websocket
        self._config = config
        self._token = token
        self._version = version
        self._log = logger
        self._on_authenticated = on_authenticated
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._last_pong = time.monotonic()
        self._binary_rejected = False
        self.role: str | None = None
        self.client_version: str | None = None

    async def run(self) -> None:
        """执行完整连接生命周期；所有异常都在内部收敛。"""
        await self._ws.accept()
        if not self._is_loopback():
            self._log.warning("rejected non-loopback client")
            await self._close(CLOSE_POLICY)
            return

        try:
            hello = await self._await_hello()
            if hello is None:
                return
            # Role 是 RootModel，日志里需要裸字符串
            self.role = str(hello.payload.role.root)
            self.client_version = hello.payload.client_version
            self._log.info("client authenticated", extra={"role": self.role, "client": hello.payload.client_name})
            if self._on_authenticated is not None:
                self._on_authenticated(self.role)

            await self._send(
                make_system_ready(
                    server_version=self._version,
                    capabilities=[],
                    request_id=hello.request_id,
                )
            )
            heartbeat = asyncio.create_task(self._heartbeat())
            try:
                await self._receive_loop()
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
        except WebSocketDisconnect:
            self._log.info("client disconnected")
        except Exception:
            # 传输层错误类型由 uvicorn/starlette 决定，无法穷举；视为连接结束。
            self._log.exception("connection failed unexpectedly")
        finally:
            await self._close(CLOSE_NORMAL)
            if self.role is not None:
                self._log.info("connection closed", extra={"role": self.role})

    # ------------------------------------------------------------------ 握手

    def _is_loopback(self) -> bool:
        client = self._ws.client
        return bool(client and client.host in LOOPBACK_HOSTS)

    async def _await_hello(self) -> HelloEvent | None:
        """等待并校验首帧。失败时已回错误并关闭连接。"""
        timeout = self._config.server.auth_timeout_ms / 1000
        try:
            message = await asyncio.wait_for(self._ws.receive(), timeout=timeout)
        except TimeoutError:
            await self._fail("auth_timeout", "hello was not received in time", CLOSE_POLICY)
            return None
        except WebSocketDisconnect:
            return None

        if message.get("type") == "websocket.disconnect":
            return None
        if message.get("text") is None:
            await self._fail("invalid_envelope", "first message must be a text hello", CLOSE_PROTOCOL)
            return None

        raw: str = message["text"]
        if len(raw.encode("utf-8")) > self._config.server.max_message_bytes:
            await self._fail("invalid_envelope", "hello exceeds max message size", CLOSE_TOO_LARGE)
            return None

        try:
            event = parse_event(raw)
        except ProtocolViolation as exc:
            close_code = CLOSE_PROTOCOL if exc.code in FATAL_CODES else CLOSE_POLICY
            await self._fail(exc.code, exc.message, close_code)
            return None

        if not isinstance(event, HelloEvent):
            await self._fail("invalid_envelope", "first message must be hello", CLOSE_PROTOCOL)
            return None
        if not tokens_equal(self._token, event.payload.token):
            await self._fail("auth_failed", "invalid token", CLOSE_POLICY, request_id=event.request_id)
            return None
        return event

    # ------------------------------------------------------------------ 收发

    async def _receive_loop(self) -> None:
        while not self._closed:
            message = await self._ws.receive()
            if message.get("type") == "websocket.disconnect":
                return
            text = message.get("text")
            data = message.get("bytes")
            if text is not None:
                await self._handle_text(text)
            elif data is not None:
                await self._handle_binary(len(data))

    async def _handle_text(self, raw: str) -> None:
        if len(raw.encode("utf-8")) > self._config.server.max_message_bytes:
            await self._fail("invalid_envelope", "message exceeds max size", CLOSE_TOO_LARGE)
            return
        try:
            event = parse_event(raw)
        except ProtocolViolation as exc:
            if exc.code in FATAL_CODES:
                await self._fail(exc.code, exc.message, CLOSE_PROTOCOL)
                return
            await self._send(
                make_system_error(
                    code=exc.code,
                    message=exc.message,
                    retryable=False,
                )
            )
            return
        await self._dispatch(event)

    async def _handle_binary(self, size: int) -> None:
        """Phase 1 不接受音频帧；只拒绝一次，避免刷屏。"""
        if self._binary_rejected:
            return
        self._binary_rejected = True
        self._log.warning("binary frame rejected", extra={"bytes": size})
        await self._send(
            make_system_error(
                code="not_implemented",
                message="binary audio frames arrive in Phase 2",
                retryable=False,
            )
        )

    async def _dispatch(self, event: ConcreteEvent) -> None:
        request_id = getattr(event, "request_id", None)
        if isinstance(event, PingEvent):
            await self._send(make_pong(event.payload.nonce))
            return
        if isinstance(event, PongEvent):
            self._last_pong = time.monotonic()
            return
        if isinstance(event, HelloEvent):
            await self._send(
                make_system_error(
                    code="invalid_envelope",
                    message="connection is already authenticated",
                    retryable=False,
                    request_id=request_id,
                )
            )
            return
        if isinstance(event, (CaptureStartEvent, CaptureStopEvent, AudioStatusEvent)):
            await self._send(
                make_system_error(
                    code="not_implemented",
                    message=f"{event.type} is implemented in Phase 2",
                    retryable=False,
                    request_id=request_id,
                )
            )
            return
        await self._send(
            make_system_error(
                code="unsupported_event",
                message=f"client must not send {event.type}",
                retryable=False,
                request_id=request_id,
            )
        )

    # ------------------------------------------------------------------ 心跳

    async def _heartbeat(self) -> None:
        interval = self._config.server.heartbeat_interval_ms / 1000
        timeout = self._config.server.heartbeat_timeout_ms / 1000
        while not self._closed:
            await asyncio.sleep(interval)
            if self._closed:
                return
            if time.monotonic() - self._last_pong > timeout:
                self._log.warning("heartbeat timeout")
                await self._close(CLOSE_GONE)
                return
            await self._send(make_ping())

    # ------------------------------------------------------------------ 发送

    async def _send(self, event: Any) -> None:
        async with self._send_lock:
            if self._closed:
                return
            try:
                await self._ws.send_text(dumps(event))
            except Exception as error:
                # 对端已断开时发送必然失败，按连接已关闭处理。
                self._closed = True
                self._log.debug("websocket send failed", extra={"error": type(error).__name__})

    async def _fail(
        self,
        code: ErrorCode,
        message: str,
        close_code: int,
        *,
        request_id: str | None = None,
    ) -> None:
        self._log.warning("connection rejected", extra={"code": code})
        await self._send(
            make_system_error(code=code, message=message, retryable=False, request_id=request_id)
        )
        await self._close(close_code)

    async def _close(self, code: int) -> None:
        if self._closed:
            return
        self._closed = True
        # 关闭是尽力而为：对端可能已经消失。
        with contextlib.suppress(Exception):
            await self._ws.close(code=code)


__all__ = ["CLOSE_NORMAL", "LOOPBACK_HOSTS", "PROTOCOL_VERSION", "EventConnection"]
