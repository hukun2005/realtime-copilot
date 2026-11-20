"""音频二进制通道：真实 uvicorn + 真实 WebSocket 客户端。"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
import websockets
from support import audio_frame, corrupt_version, event, expect_closed, hello_event, recv_until

pytestmark = pytest.mark.asyncio


async def read_health(server) -> dict:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{server.http_url}/healthz")
    return response.json()



async def wait_audio(server, predicate, *, seconds: float = 5.0) -> dict:  # type: ignore[no-untyped-def]
    """轮询音频统计直到 predicate 成立。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    last: dict = {}
    while loop.time() < deadline:
        body = await read_health(server)
        last = body.get("audio", {})
        if predicate(last):
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"audio condition not met within {seconds}s; last={last}")


async def connect_audio(server):  # type: ignore[no-untyped-def]
    socket = await websockets.connect(server.url)
    await socket.send(hello_event(server.token, role="audio"))
    await recv_until(socket, "system.ready")
    return socket


async def test_audio_role_streams_frames(live_server) -> None:  # type: ignore[no-untyped-def]
    async with await connect_audio(live_server) as socket:
        for index in range(1, 101):
            await socket.send(audio_frame(index, 1_000_000_000 + index * 20_000_000))

        stats = await wait_audio(live_server, lambda s: s["frames"] == 100)
        assert stats["bytes"] == 100 * 640
        assert stats["sinkFrames"] == 100
        assert stats["sessions"] == 1
        assert stats["gaps"] == 0

        health = await read_health(live_server)
        assert health["roles"]["audio"] == 1


async def test_gap_detection_counts_missing_frames(live_server) -> None:  # type: ignore[no-untyped-def]
    async with await connect_audio(live_server) as socket:
        for sequence in (1, 2, 5, 6):
            await socket.send(audio_frame(sequence, 1_000_000_000 + sequence * 20_000_000))

        stats = await wait_audio(live_server, lambda s: s["frames"] == 4)
        assert stats["gaps"] == 2
        assert stats["gapEvents"] == 1


async def test_duplicate_sequence_is_dropped(live_server) -> None:  # type: ignore[no-untyped-def]
    async with await connect_audio(live_server) as socket:
        await socket.send(audio_frame(1, 1_000_000_000))
        await socket.send(audio_frame(1, 1_000_020_000))

        stats = await wait_audio(live_server, lambda s: s["duplicates"] == 1)
        assert stats["frames"] == 1


async def test_invalid_frame_is_dropped_without_closing(live_server) -> None:  # type: ignore[no-untyped-def]
    async with await connect_audio(live_server) as socket:
        await socket.send(corrupt_version(audio_frame(1, 1_000_000_000)))
        stats = await wait_audio(live_server, lambda s: s["invalid"] == 1)
        assert stats["errorCodes"] == {"unsupported_version": 1}

        # 连接仍然可用：随后发一个合法帧
        await socket.send(audio_frame(2, 1_000_020_000))
        stats = await wait_audio(live_server, lambda s: s["frames"] == 1)
        assert stats["dropped"] == 1


async def test_oversized_binary_message_closes_connection(live_server) -> None:  # type: ignore[no-untyped-def]
    async with await connect_audio(live_server) as socket:
        await socket.send(b"\x00" * 5000)
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "invalid_payload"
        await expect_closed(socket)

    stats = await wait_audio(live_server, lambda s: s["tooLarge"] == 1)
    assert stats["frames"] == 0


async def test_rate_limit_closes_connection(live_server) -> None:  # type: ignore[no-untyped-def]
    async with await connect_audio(live_server) as socket:
        for index in range(400):
            await socket.send(audio_frame(index + 1, 1_000_000_000 + index * 1_000_000))

        error = await recv_until(socket, "system.error", seconds=5.0)
        assert error["payload"]["code"] == "rate_limited"
        await expect_closed(socket)

    stats = await wait_audio(live_server, lambda s: s["rateLimited"] > 0)
    assert stats["rateLimited"] > 0


async def test_audio_role_cannot_start_capture(live_server) -> None:  # type: ignore[no-untyped-def]
    async with await connect_audio(live_server) as socket:
        await socket.send(
            event(
                "capture.start",
                {"loopback": {"deviceId": None, "processId": None}, "mic": {"enabled": False}},
                request_id="req_audio000000000000000000",
            )
        )
        error = await recv_until(socket, "system.error")
        assert error["payload"]["code"] == "invalid_role"

        # 拒绝命令不应断开音频流
        await socket.send(audio_frame(1, 1_000_000_000))
        await wait_audio(live_server, lambda s: s["frames"] == 1)


async def test_timestamp_zero_is_invalid(live_server) -> None:  # type: ignore[no-untyped-def]
    async with await connect_audio(live_server) as socket:
        await socket.send(audio_frame(1, 0))
        stats = await wait_audio(live_server, lambda s: s["invalid"] == 1)
        assert stats["errorCodes"] == {"timestamp_invalid": 1}
        assert stats["frames"] == 0


async def test_audio_disconnect_releases_session(live_server) -> None:  # type: ignore[no-untyped-def]
    socket = await connect_audio(live_server)
    await socket.send(audio_frame(1, 1_000_000_000))
    await wait_audio(live_server, lambda s: s["sessions"] == 1)
    await socket.close()

    await wait_audio(live_server, lambda s: s["sessions"] == 0)
    health = await read_health(live_server)
    assert health["roles"].get("audio", 0) == 0
    # 统计不因断开而清零
    assert health["audio"]["frames"] == 1


async def test_binary_frame_content_is_never_logged(live_server, caplog) -> None:  # type: ignore[no-untyped-def]
    """日志中不得出现 PCM 字节或令牌。"""
    caplog.set_level(logging.INFO, logger="copilot_core")

    async with await connect_audio(live_server) as socket:
        await socket.send(audio_frame(1, 1_000_000_000))
        await wait_audio(live_server, lambda s: s["frames"] == 1)

    # 只看本服务自己的日志；第三方库（websockets/httpcore）的调试输出不在此列。
    records = [record.getMessage() for record in caplog.records if record.name.startswith("copilot_core")]
    assert records, "expected at least one application log record"
    text = "\n".join(records)
    assert live_server.token not in text
    assert "pcm" not in text.lower()
    assert "payload" not in text.lower()
