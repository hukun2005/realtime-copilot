"""30 分钟真实 WASAPI + Paraformer 联合测试驱动。

前置：Core 已启动（RTC_ASR_PROVIDER=aliyun），runtime.json 可读，系统正在播放音频。
本脚本只负责控制面 start/stop、周期采样与报告，不伪造任何结果。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import platform
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import websockets


def _event_id() -> str:
    return "evt_" + uuid.uuid4().hex


def _envelope(event_type: str, payload: dict[str, Any], request_id: str | None = None) -> str:
    data: dict[str, Any] = {
        "v": 1,
        "type": event_type,
        "eventId": _event_id(),
        "ts": int(time.time() * 1000),
        "payload": payload,
    }
    if request_id is not None:
        data["requestId"] = request_id
    return json.dumps(data)


def _healthz(port: int) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _git() -> tuple[str, bool]:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
        return sha, dirty
    except Exception:
        return "unknown", True


async def wait_for(ws: Any, predicate, *, deadline_seconds: float) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + deadline_seconds
    while asyncio.get_running_loop().time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=deadline - asyncio.get_running_loop().time())
        message = json.loads(raw)
        if predicate(message):
            return message
    raise TimeoutError("condition not met")


async def run(runtime: dict[str, Any], minutes: float, interval: float, mic: bool) -> dict[str, Any]:
    port = int(runtime["port"])
    token = runtime["token"]
    ws_url = f"ws://127.0.0.1:{port}/ws/v1/events"

    samples: list[dict[str, Any]] = []
    started = time.monotonic()
    started_wall = time.time()
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(_envelope("hello", {"token": token, "role": "ui", "clientVersion": "soak"}))
        await wait_for(ws, lambda m: m.get("type") == "system.ready", deadline_seconds=15)

        request_id = "req_" + uuid.uuid4().hex
        await ws.send(
            _envelope(
                "capture.start",
                {"loopback": {"deviceId": None, "processId": None}, "mic": {"enabled": mic}},
                request_id,
            )
        )
        status = await wait_for(
            ws,
            lambda m: m.get("type") == "audio.status"
            and m.get("payload", {}).get("state") in ("capturing", "error"),
            deadline_seconds=30,
        )
        if status["payload"]["state"] != "capturing":
            raise RuntimeError(f"capture failed: {status['payload']}")

        async def heartbeat() -> None:
            while True:
                try:
                    raw = await ws.recv()
                except Exception:
                    return
                message = json.loads(raw)
                if message.get("type") == "ping":
                    await ws.send(
                        _envelope(
                            "pong",
                            {"nonce": (message.get("payload") or {}).get("nonce")},
                            message.get("requestId"),
                        )
                    )

        hb = asyncio.create_task(heartbeat())
        deadline = started + minutes * 60
        while time.monotonic() < deadline:
            snapshot = await asyncio.to_thread(_healthz, port)
            if snapshot is not None:
                samples.append(
                    {
                        "at": round(time.monotonic() - started, 1),
                        "audio": snapshot.get("audio"),
                        "asr": snapshot.get("asr"),
                        "process": snapshot.get("process"),
                    }
                )
            await asyncio.sleep(interval)

        stop_id = "req_" + uuid.uuid4().hex
        await ws.send(_envelope("capture.stop", {}, stop_id))
        await asyncio.sleep(2)
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await hb

    loopback = [
        (sample["audio"] or {}).get("sources", {}).get("loopback", {}) for sample in samples
    ]
    frames = [item.get("framesReceived", 0) for item in loopback if item]
    first = next((item for item in loopback if item and item.get("framesReceived", 0) > 0), None)
    last = next((item for item in reversed(loopback) if item and item.get("framesReceived", 0) > 0), None)
    asr_last = (samples[-1]["asr"] if samples else None) or {}
    vad_last = ((samples[-1]["audio"] or {}).get("vad") if samples else None) or {}
    metrics = asr_last.get("metrics") or {}
    process = (samples[-1]["process"] if samples else None) or {}
    sha, dirty = _git()
    wall_seconds = time.time() - started_wall
    expected_frames = int(minutes * 60 * 50 * 0.9)
    frame_ratio = round((frames[-1] if frames else 0) / max(1, expected_frames), 3)
    wall_ratio = round(wall_seconds / max(1.0, minutes * 60), 3)
    valid = frame_ratio >= 1.0 and wall_ratio >= 0.95

    return {
        "result": "PASS" if valid and asr_last.get("finals", 0) > 0 else "FAIL",
        "mode": "real-wasapi" + ("-loopback+mic" if mic else "-loopback") + " + aliyun-paraformer",
        "minutes": minutes,
        "gitSha": sha,
        "gitDirty": dirty,
        "windowsBuild": platform.version(),
        "model": "paraformer-realtime-v2",
        "framesReceived": frames[-1] if frames else 0,
        "framesDelta": (frames[-1] - frames[0]) if len(frames) >= 2 else 0,
        "wallSeconds": round(wall_seconds, 1),
        "wallRatio": wall_ratio,
        "expectedFrames": expected_frames,
        "frameRatio": frame_ratio,
        "firstFrameAt": first,
        "lastFrameAt": last,
        "segmentsEmitted": vad_last.get("segmentsEmitted"),
        "segmentsDropped": vad_last.get("segmentsDropped"),
        "asrPartials": asr_last.get("partials"),
        "asrFinals": asr_last.get("finals"),
        "asrErrors": asr_last.get("errors"),
        "asrBySource": asr_last.get("bySource"),
        "asrState": asr_last.get("state"),
        "finalP50Ms": metrics.get("finalP50Ms"),
        "finalP95Ms": metrics.get("finalP95Ms"),
        "firstPartialP50Ms": metrics.get("firstPartialP50Ms"),
        "firstPartialP95Ms": metrics.get("firstPartialP95Ms"),
        "latencySamples": metrics.get("latencySamples"),
        "connectionAttempts": metrics.get("connection_attempts"),
        "connectionFailures": metrics.get("connection_failures"),
        "pcmPeakBytes": vad_last.get("peakPcmBytesInFlight"),
        "process": process,
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--mic", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("artifacts/asr-soak/wasapi-soak.json"))
    args = parser.parse_args()

    runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
    report = asyncio.run(run(runtime, args.minutes, args.interval, args.mic))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {key: report[key] for key in report if key != "samples"}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
