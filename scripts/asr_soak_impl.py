"""ASR 30/60 分钟 soak 实现（模拟音频；不伪造真实运行结果）。

用法：
  uv run python scripts/asr_soak_impl.py --minutes 30 --sources 2

本脚本只驱动 AsrPipeline + VAD，用于观察 PCM 预算、队列深度与进程内存；
它不连接 Deepgram，也不代表真实会议识别结果。未配置 API Key 时不涉及真实 API。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import struct
import time
from pathlib import Path

from copilot_core.asr.pipeline import AsrPipeline
from copilot_core.asr.vad import SOURCES, VadFrame
from copilot_core.memory import process_memory
from copilot_core.pcm_level import peak_dbfs, rms_dbfs


def frame(source: str, sequence: int, ts_ns: int, loud: bool) -> VadFrame:
    amplitude = 8000 if loud else 0
    pcm = struct.pack("<320h", *([amplitude, -amplitude] * 160))
    return VadFrame(
        source=source,
        sequence=sequence,
        capture_ts_ns=ts_ns,
        pcm16=pcm,
        sample_rate=16000,
        channels=1,
        frame_duration_ms=20,
        peak_dbfs=peak_dbfs(pcm),
        rms_dbfs=rms_dbfs(pcm),
    )


async def run(minutes: float, sources: int) -> dict[str, object]:
    pipeline = AsrPipeline(queue_frames=500, queue_segments=64)
    pipeline.start()
    started = time.monotonic()
    names = SOURCES[:sources]
    sequences = {name: 0 for name in names}
    consumers: list[asyncio.Task[None]] = []

    async def consume(name: str) -> None:
        while True:
            await pipeline.get_segment(name)
            # 模拟 ASR 消费；真实 Provider 不在本脚本内。
            await asyncio.sleep(0.05)

    for name in sequences:
        consumers.append(asyncio.create_task(consume(name)))

    frames = int(minutes * 60 * 50)
    try:
        for _ in range(frames):
            for name in sequences:
                sequence = sequences[name]
                sequences[name] += 1
                loud = (sequence // 50) % 2 == 0
                pipeline.ingest(frame(name, sequence, int(time.monotonic() * 1e9), loud))
            await asyncio.sleep(0.02)
    finally:
        for task in consumers:
            task.cancel()
        for task in consumers:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        snapshot = pipeline.snapshot()
        await pipeline.close()

    return {
        "status": "PASS",
        "mode": "simulated-vad-only",
        "minutes": minutes,
        "sources": sources,
        "frames": frames * sources,
        "elapsedSeconds": round(time.monotonic() - started, 1),
        "pcmBytesQueued": snapshot["pcmBytesQueued"],
        "pcmBytesProcessing": snapshot["pcmBytesProcessing"],
        "pcmBytesInFlight": snapshot["pcmBytesInFlight"],
        "peakPcmBytesInFlight": snapshot["peakPcmBytesInFlight"],
        "pcmBytesDropped": snapshot["pcmBytesDropped"],
        "segmentsEmitted": snapshot["segmentsEmitted"],
        "segmentsDropped": snapshot["segmentsDropped"],
        "process": process_memory(),
        "note": "simulated VAD pipeline only; NOT a real meeting or Deepgram run",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--sources", type=int, default=2)
    args = parser.parse_args()

    report = asyncio.run(run(args.minutes, max(1, args.sources)))
    output = Path("artifacts/asr-soak")
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"soak-{int(time.time())}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({**report, "report": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
