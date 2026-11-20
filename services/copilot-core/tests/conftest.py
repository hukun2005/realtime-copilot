"""Core 测试夹具：真实进程内 uvicorn（真 socket），不使用模拟连接。"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
from copilot_core.app import create_app
from copilot_core.config import AppConfig
from copilot_core.security import generate_token
from fastapi.testclient import TestClient


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """独立数据目录。"""
    return tmp_path


@pytest.fixture
def token() -> str:
    """每次测试一个新令牌。"""
    return generate_token()


@pytest.fixture
def config() -> AppConfig:
    """测试用配置：短鉴权超时与短心跳，保证失败用例快速结束。"""
    return AppConfig.model_validate(
        {
            "mode": "standalone",
            "server": {
                "host": "127.0.0.1",
                "port": 0,
                "auth_timeout_ms": 400,
                "heartbeat_interval_ms": 300,
                "heartbeat_timeout_ms": 1200,
            },
        }
    )


@pytest.fixture
def client(config: AppConfig, token: str, data_dir: Path) -> Iterator[TestClient]:
    """带 lifespan 的 HTTP 客户端（仅用于 /healthz 等 HTTP 面）。"""
    app = create_app(config, token=token, data_dir=data_dir, version="test")
    with TestClient(app) as test_client:
        yield test_client


@dataclass(frozen=True)
class LiveServer:
    """一条真实监听中的服务。"""

    port: int
    token: str
    runtime_path: Path

    @property
    def url(self) -> str:
        """WebSocket 地址。"""
        return f"ws://127.0.0.1:{self.port}/ws/v1/events"

    @property
    def http_url(self) -> str:
        """HTTP 基地址。"""
        return f"http://127.0.0.1:{self.port}"


@pytest_asyncio.fixture
async def live_server(config: AppConfig, token: str, data_dir: Path) -> AsyncIterator[LiveServer]:
    """启动真实 uvicorn 服务，yield 后优雅关闭并校验运行时文件被清理。"""
    runtime_path = data_dir / "runtime.json"
    app = create_app(config, token=token, data_dir=data_dir, version="test", runtime_path=runtime_path)

    listener = socket.create_server(("127.0.0.1", 0))
    port = int(listener.getsockname()[1])
    app.state.port = port

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
            server_header=False,
            lifespan="on",
            ws="websockets-sansio",
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.02)
    assert server.started, "uvicorn did not start"

    yield LiveServer(port=port, token=token, runtime_path=runtime_path)

    server.should_exit = True
    await asyncio.wait_for(task, timeout=10)
    listener.close()
    assert not runtime_path.exists(), "graceful shutdown must remove runtime.json"
