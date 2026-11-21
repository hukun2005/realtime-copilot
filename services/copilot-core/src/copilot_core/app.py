"""FastAPI 应用装配：``/healthz``、``/ws/v1/events`` 与采集控制面。

应用本身不负责绑定端口（见 :mod:`copilot_core.runtime`），以便测试直接注入
配置与临时数据目录。
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

from copilot_protocol import PROTOCOL_VERSION

from .audio import AudioHub
from .capture import CaptureController
from .config import AppConfig
from .logging_setup import get_logger
from .security import build_runtime_info, remove_runtime_file, write_runtime_file
from .storage import close, open_database
from .tickets import TicketIssuer
from .ws.connection import EventConnection

logger = get_logger("copilot_core.app")


def create_app(
    config: AppConfig,
    *,
    token: str,
    data_dir: Path,
    version: str,
    runtime_path: Path | None = None,
) -> FastAPI:
    """构造 FastAPI 应用。

    Args:
        config: 已校验配置。
        token: 本次运行的鉴权令牌。
        data_dir: 数据目录（SQLite 与 ``runtime.json`` 的默认位置）。
        version: 服务版本。
        runtime_path: 若非 ``None``，启动时写入、退出时删除该运行时文件。

    Returns:
        配置好的应用；``/docs`` 等自动文档已关闭，减少暴露面。
    """
    ui_connections: set[EventConnection] = set()

    async def broadcast(event: Any) -> None:
        """把状态事件广播给所有 UI/bridge 连接（尽力而为）。"""
        for connection in list(ui_connections):
            try:
                await connection.send_event(event)
            except Exception:
                ui_connections.discard(connection)

    tickets = TicketIssuer(ttl_seconds=config.capture.ticket_ttl_seconds)
    controller = CaptureController(
        config=config.capture,
        tickets=tickets,
        logger=get_logger("copilot_core.capture"),
        broadcast=broadcast,
        source_stats=lambda: app.state.audio.snapshot()["sources"],
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = open_database(config.database_path(data_dir))
        app.state.db = database
        app.state.started_at = time.monotonic()
        if runtime_path is not None:
            info = build_runtime_info(
                version=version,
                protocol=PROTOCOL_VERSION,
                host=config.server.host,
                port=int(getattr(app.state, "port", 0)),
                token=token,
                mode=config.mode,
                data_dir=data_dir,
            )
            hardened = write_runtime_file(runtime_path, info)
            logger.info(
                "runtime file written",
                extra={"path": str(runtime_path), "hardened": hardened, "mode": config.mode},
            )
        logger.info("core started", extra={"version": version, "database": str(config.database_path(data_dir))})
        try:
            yield
        finally:
            await controller.shutdown()
            close(database)
            if runtime_path is not None:
                remove_runtime_file(runtime_path)
            logger.info("core stopped")

    app = FastAPI(
        title="RealTime Copilot Core",
        version=version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.connections = 0
    app.state.roles = {}
    app.state.port = 0
    app.state.audio = AudioHub(config.audio)
    app.state.capture = controller

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """健康检查。

        Returns:
            200 表示数据库可用；503 表示降级。
        """
        started_at = getattr(app.state, "started_at", None)
        uptime_ms = int((time.monotonic() - started_at) * 1000) if started_at is not None else 0
        database_ok = True
        try:
            app.state.db.execute("SELECT 1")
        except sqlite3.Error:
            database_ok = False
        try:
            body = {
            "status": "ok" if database_ok else "degraded",
            "version": version,
            "protocol": PROTOCOL_VERSION,
            "mode": config.mode,
            "uptimeMs": uptime_ms,
            "connections": int(app.state.connections),
            "roles": dict(app.state.roles),
            "audio": app.state.audio.snapshot(),
            "capture": controller.snapshot(),
                "database": "ok" if database_ok else "error",
            }
            return JSONResponse(body, status_code=200 if database_ok else 503)
        except Exception:
            logger.exception("healthz failed")
            return JSONResponse({"status": "error", "error": "internal_error"}, status_code=500)

    @app.websocket("/ws/v1/events")
    async def events(websocket: WebSocket) -> None:
        """事件通道：一条连接对应一个客户端角色。"""

        app.state.connections += 1
        connection: EventConnection | None = None

        def on_authenticated(role: str) -> None:
            app.state.roles[role] = app.state.roles.get(role, 0) + 1
            if role in ("ui", "bridge") and connection is not None:
                ui_connections.add(connection)

        connection = EventConnection(
            websocket,
            config=config,
            token=token,
            version=version,
            logger=logger,
            on_authenticated=on_authenticated,
            audio_hub=app.state.audio,
            capture=controller,
            tickets=tickets,
        )
        try:
            await connection.run()
        finally:
            ui_connections.discard(connection)
            app.state.connections -= 1
            role = connection.role
            if role is not None:
                app.state.roles[role] = max(0, app.state.roles.get(role, 1) - 1)

    return app
