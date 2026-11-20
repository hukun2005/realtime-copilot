"""控制面测试客户端：以 ui 角色驱动 capture.start / capture.stop。

用法::

    python scripts/control_client.py --runtime <runtime.json> start
    python scripts/control_client.py --runtime <runtime.json> stop
    python scripts/control_client.py --runtime <runtime.json> session --seconds 10

所有输出是一行 JSON，便于 PowerShell 解析。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import websockets

from copilot_protocol import make_event, parse_event

CAPTURING = "capturing"
IDLE = "idle"
ERROR = "error"


def load_runtime(path: Path) -> tuple[int, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return int(payload["port"]), str(payload["token"])


async def connect(url: str, token: str):  # type: ignore[no-untyped-def]
    socket = await websockets.connect(url)
    hello = make_event(
        "hello",
        {
            "token": token,
            "role": "ui",
            "clientVersion": "0.1.0",
            "clientName": "control-client",
        },
    )
    await socket.send(json.dumps(hello.model_dump(by_alias=True, exclude_none=True)))
    while True:
        raw = await asyncio.wait_for(socket.recv(), timeout=10)
        event = parse_event(raw)
        if event.type == "system.ready":
            return socket
        if event.type == "system.error":
            raise RuntimeError(f"auth failed: {event.payload.model_dump()}")


async def wait_state(socket, states: set[str], *, seconds: float) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=max(0.1, deadline - time.monotonic()))
        except TimeoutError:
            break
        event = parse_event(raw)
        if event.type == "audio.status":
            last = event.payload.model_dump(by_alias=True)
            if last.get("state") in states:
                return last
        if event.type == "system.error":
            last = {"error": event.payload.model_dump(by_alias=True)}
            if "error" in states:
                return last
    raise TimeoutError(f"did not observe {sorted(states)}; last={last}")


async def run(action: str, runtime: Path, seconds: float) -> dict[str, Any]:
    port, token = load_runtime(runtime)
    base = f"ws://127.0.0.1:{port}/ws/v1/events"
    socket = await connect(base, token)
    result: dict[str, Any] = {"action": action}
    try:
        started_at = time.monotonic()
        if action == "stop":
            stop_command = make_event("capture.stop", {}, "req_" + "1" * 32)
            await socket.send(json.dumps(stop_command.model_dump(by_alias=True, exclude_none=True)))
            result["stop"] = await wait_state(socket, {IDLE, ERROR}, seconds=15)
            async with httpx.AsyncClient() as client:
                health = (await client.get(f"http://127.0.0.1:{port}/healthz")).json()
            result["health"] = {"audio": health["audio"], "capture": health["capture"]}
            result["ok"] = result["stop"].get("state") == IDLE and health["capture"]["state"] == IDLE
            return result
        start_command = make_event(
            "capture.start",
            {"loopback": {"deviceId": None, "processId": None}, "mic": {"enabled": False}},
            "req_" + "0" * 32,
        )
        await socket.send(json.dumps(start_command.model_dump(by_alias=True, exclude_none=True)))
        status = await wait_state(socket, {CAPTURING, ERROR}, seconds=20)
        result["start"] = status

        if action == "start":
            return result
        if status.get("state") != CAPTURING:
            result["ok"] = False
            return result

        if seconds > 0:
            await asyncio.sleep(seconds)

        stop_command = make_event("capture.stop", {}, "req_" + "1" * 32)
        await socket.send(json.dumps(stop_command.model_dump(by_alias=True, exclude_none=True)))
        result["stop"] = await wait_state(socket, {IDLE, ERROR}, seconds=15)

        async with httpx.AsyncClient() as client:
            health = (await client.get(f"http://127.0.0.1:{port}/healthz")).json()
        result["health"] = {"audio": health["audio"], "capture": health["capture"]}
        result["durationMs"] = int((time.monotonic() - started_at) * 1000)
        result["ok"] = (
            result["start"].get("state") == CAPTURING
            and result["stop"].get("state") == IDLE
            and health["capture"]["state"] == IDLE
        )
        return result
    finally:
        await socket.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "session"])
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=0)
    arguments = parser.parse_args()


    try:
        payload = asyncio.run(run(arguments.action, arguments.runtime, arguments.seconds))
    except Exception as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}))
        return 1

    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
