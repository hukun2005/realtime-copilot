"""监听与运行：显式创建监听 socket，先拿到真实端口再启动 uvicorn。

``port = 0`` 时由操作系统分配端口；先绑定再启动可以保证写入 ``runtime.json``
的端口与实际监听端口一致，客户端无需猜测。
"""

from __future__ import annotations

import socket
from typing import Any

import uvicorn

from .config import AppConfig


def create_listening_socket(host: str, port: int) -> socket.socket:
    """创建已绑定并监听的 TCP socket。

    Args:
        host: 监听地址（配置层已限定为 127.0.0.1）。
        port: 端口，0 表示由系统分配。

    Returns:
        已 ``listen`` 的 socket。
    """
    return socket.create_server((host, port), reuse_port=False)


def run_server(app: Any, config: AppConfig) -> None:
    """阻塞运行 HTTP/WebSocket 服务，直到收到退出信号。

    Args:
        app: FastAPI 应用。
        config: 已校验配置。
    """
    listener = create_listening_socket(config.server.host, config.server.port)
    port = int(listener.getsockname()[1])
    app.state.port = port
    server_config = uvicorn.Config(
        app,
        host=config.server.host,
        port=port,
        log_config=None,
        access_log=False,
        server_header=False,
        lifespan="on",
        ws="websockets-sansio",
    )
    server = uvicorn.Server(server_config)
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
