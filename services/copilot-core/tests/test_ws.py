"""WebSocket 端到端测试：真实 uvicorn 监听 + 真实 TCP 客户端。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import websockets
from support import event, expect_closed, hello_event, recv_until

pytestmark = pytest.mark.asyncio


async def test_handshake_returns_ready(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(
            hello_event(live_server.token, request_id="req_handshake00000000000000")
        )
        ready = await recv_until(socket, "system.ready")
        assert ready["payload"]["serverVersion"] == "test"
        assert ready["payload"]["protocol"] == 1
        assert ready["payload"]["capabilities"] == []
        assert ready["requestId"] == "req_handshake00000000000000"
        assert ready["v"] == 1


async def test_wrong_token_is_rejected_and_closed(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(hello_event("0" * 64))
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "auth_failed"
        assert error["payload"]["retryable"] is False
        await expect_closed(socket)


async def test_protocol_version_mismatch_is_rejected(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(
            json.dumps(
                {
                    "v": 2,
                    "type": "hello",
                    "eventId": "evt_v2hello0000000000000000000",
                    "ts": 1763700000000,
                    "payload": {"token": live_server.token, "role": "ui", "clientVersion": "test"},
                }
            )
        )
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "protocol_version_unsupported"
        await expect_closed(socket)


async def test_first_message_must_be_hello(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(event("ping", {}))
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "invalid_envelope"
        await expect_closed(socket)


async def test_missing_hello_times_out(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        error = await recv_until(socket, "system.error", seconds=5.0)
        assert error["payload"]["code"] == "auth_timeout"
        await expect_closed(socket)


async def test_ping_pong_roundtrip(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(hello_event(live_server.token))
        await recv_until(socket, "system.ready")

        await socket.send(event("ping", {"nonce": "abc"}))
        pong = await recv_until(socket, "pong")
        assert pong["payload"]["nonce"] == "abc"


async def test_unknown_event_keeps_connection_open(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(hello_event(live_server.token))
        await recv_until(socket, "system.ready")

        await socket.send(event("meeting.summary", {}))
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "unsupported_event"

        # 仍未关闭：一次 ping/pong 即可证明
        await socket.send(event("ping", {"nonce": "still-alive"}))
        pong = await recv_until(socket, "pong")
        assert pong["payload"]["nonce"] == "still-alive"


async def test_capture_start_is_not_implemented(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(hello_event(live_server.token))
        await recv_until(socket, "system.ready")

        await socket.send(
            event(
                "capture.start",
                {"loopback": {"deviceId": None, "processId": None}, "mic": {"enabled": False}},
                request_id="req_capture0000000000000000",
            )
        )
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "not_implemented"
        assert error["requestId"] == "req_capture0000000000000000"


async def test_binary_frames_are_rejected(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(hello_event(live_server.token))
        await recv_until(socket, "system.ready")

        await socket.send(b"\x00\x01\x02\x03")
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "not_implemented"


async def test_oversized_message_closes_connection(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(hello_event(live_server.token))
        await recv_until(socket, "system.ready")

        await socket.send("x" * 300_000)
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "invalid_envelope"
        await expect_closed(socket)


async def test_healthz_over_real_socket(live_server) -> None:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{live_server.http_url}/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "test"


async def test_runtime_file_describes_live_endpoint(live_server, data_dir: Path) -> None:  # type: ignore[no-untyped-def]
    runtime = json.loads((data_dir / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["token"] == live_server.token
    assert runtime["port"] == live_server.port
    assert runtime["host"] == "127.0.0.1"
    assert runtime["wsUrl"] == f"ws://127.0.0.1:{live_server.port}/ws/v1/events"
    assert runtime["protocol"] == 1


async def test_two_clients_are_counted(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as first:
        await first.send(hello_event(live_server.token))
        await recv_until(first, "system.ready")

        async with websockets.connect(live_server.url) as second:
            await second.send(hello_event(live_server.token, role="bridge"))
            await recv_until(second, "system.ready")

            async with httpx.AsyncClient() as client:
                body = (await client.get(f"{live_server.http_url}/healthz")).json()
            assert body["connections"] == 2
            assert body["roles"] == {"ui": 1, "bridge": 1}


async def test_duplicate_hello_is_rejected_after_auth(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(hello_event(live_server.token))
        await recv_until(socket, "system.ready")

        await socket.send(hello_event(live_server.token))
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "invalid_envelope"
