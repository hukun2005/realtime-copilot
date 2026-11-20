"""跨测试共享的 WebSocket 客户端小工具。"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

import pytest
from copilot_core.audio import build_frame, encode_frame
from websockets.exceptions import ConnectionClosed

PONG_TEMPLATE = {
    "v": 1,
    "type": "pong",
    "eventId": "evt_pong000000000000000000000",
    "ts": 1763700000000,
    "payload": {},
}


def hello_event(token: str, *, role: str = "ui", request_id: str | None = None) -> str:
    """构造 hello 线报文。"""
    envelope: dict[str, Any] = {
        "v": 1,
        "type": "hello",
        "eventId": "evt_hello00000000000000000000",
        "ts": 1763700000000,
        "payload": {"token": token, "role": role, "clientVersion": "test"},
    }
    if request_id is not None:
        envelope["requestId"] = request_id
    return json.dumps(envelope)


def event(event_type: str, payload: dict[str, Any] | None = None, *, request_id: str | None = None) -> str:
    """构造任意事件线报文。"""
    envelope: dict[str, Any] = {
        "v": 1,
        "type": event_type,
        "eventId": "evt_event00000000000000000000",
        "ts": 1763700000000,
        "payload": payload or {},
    }
    if request_id is not None:
        envelope["requestId"] = request_id
    return json.dumps(envelope)


async def recv_until(websocket: Any, expected: str, seconds: float = 5.0) -> dict[str, Any]:
    """读取事件直到指定类型；期间自动回 pong，避免被服务端判超时。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {expected}")
        raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        parsed = json.loads(raw)
        if parsed.get("type") == "ping":
            await websocket.send(json.dumps(PONG_TEMPLATE))
            continue
        if parsed.get("type") == expected:
            return parsed
        raise AssertionError(f"expected {expected}, got {parsed.get('type')}: {parsed}")


async def expect_closed(websocket: Any, seconds: float = 3.0) -> None:
    """断言连接会被服务端关闭。"""
    with pytest.raises(ConnectionClosed):
        while True:
            await asyncio.wait_for(websocket.recv(), timeout=seconds)


def audio_frame(sequence: int, capture_ts_ns: int, *, source: str = "loopback") -> bytes:
    """构造一条符合 v1 布局的二进制帧。"""
    return encode_frame(build_frame(sequence=sequence, capture_ts_ns=capture_ts_ns, source=source))


def corrupt_version(data: bytes) -> bytes:
    """把帧的协议版本改成 2，用于测试无效帧。"""
    corrupted = bytearray(data)
    corrupted[0] = 2
    return bytes(corrupted)

def tone_frame(sequence: int, capture_ts_ns: int, *, amplitude: int = 16384) -> bytes:
    """构造一条带固定幅度方波的帧，用于电平断言。"""
    samples = [amplitude, -amplitude] * 160
    payload = struct.pack("<320h", *samples)
    return encode_frame(build_frame(sequence=sequence, capture_ts_ns=capture_ts_ns, payload=payload))