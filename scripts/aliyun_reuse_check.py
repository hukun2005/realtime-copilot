"""真实 API：验证 Paraformer 单连接顺序复用与逐 Segment 建连对比。"""

from __future__ import annotations

import asyncio
import json
import os
import time
import wave
from pathlib import Path

from copilot_core.asr import AsrProviderConfig, DashScopeParaformerProvider, SpeechSegment


def load_segments(path: Path, count: int) -> list[SpeechSegment]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        pcm = handle.readframes(handle.getnframes())
    frames = len(pcm) // 2
    chunk = frames // count
    segments = []
    for index in range(count):
        data = pcm[index * chunk * 2 : (index + 1) * chunk * 2]
        segments.append(
            SpeechSegment(
                segment_id=f"seg_reuse_{index}",
                source="loopback",
                start_ts_ns=index * chunk * 1_000_000_000 // rate,
                end_ts_ns=(index + 1) * chunk * 1_000_000_000 // rate,
                frames=len(data) // 640,
                pcm16=data,
                sample_rate=rate,
                channels=1,
                peak_dbfs=-12.0,
                rms_dbfs=-20.0,
            )
        )
    return segments


async def run(reuse: bool, segments: list[SpeechSegment]) -> dict[str, object]:
    config = AsrProviderConfig(
        name="aliyun",
        api_key_env="DASHSCOPE_API_KEY",
        workspace_id=os.environ["DASHSCOPE_WORKSPACE_ID"],
        model="paraformer-realtime-v2",
        reuse_connections=reuse,
    )
    provider = DashScopeParaformerProvider(config)
    await provider.connect("reuse-check")
    started = time.monotonic()
    finals = 0
    for segment in segments:
        async for result in provider.transcribe(segment):
            if result.final:
                finals += 1
    elapsed = round(time.monotonic() - started, 2)
    metrics = provider.metrics()
    await provider.close()
    return {
        "reuse": reuse,
        "segments": len(segments),
        "finals": finals,
        "elapsedSeconds": elapsed,
        "connectionsCreated": metrics.get("connections_created"),
        "connectionsReused": metrics.get("connections_reused"),
        "connectionAttempts": metrics.get("connection_attempts"),
        "tasksCompleted": metrics.get("tasks_completed"),
        "finalP50Ms": metrics.get("finalP50Ms"),
        "finalP95Ms": metrics.get("finalP95Ms"),
        "activeWebSockets": metrics.get("active_web_sockets"),
    }


async def main() -> int:
    segments = load_segments(Path("artifacts/tts-zh.wav"), 3)
    reused = await run(True, segments)
    legacy = await run(False, segments)
    report = {"reused": reused, "perSegmentConnection": legacy}
    print(json.dumps(report, ensure_ascii=False))
    ok = (
        reused["finals"] == 3
        and legacy["finals"] == 3
        and reused["connectionsCreated"] == 1
        and reused["tasksCompleted"] == 3
        and legacy["connectionsCreated"] == 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
