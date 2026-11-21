"""Deepgram 真实识别自检实现；无 Key 时输出 SKIP。"""

from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import sys
import time

from copilot_core.asr import (
    AsrProviderConfig,
    DeepgramProvider,
    SpeechSegment,
)


def make_pcm(seconds: float = 2.0) -> bytes:
    rate = 16000
    frames = bytearray()
    for index in range(int(rate * seconds)):
        value = int(9000 * math.sin(2 * math.pi * 440 * index / rate))
        frames += struct.pack("<h", value)
    return bytes(frames)


async def run() -> dict[str, object]:
    config = AsrProviderConfig(
        name="deepgram",
        api_key_env="DEEPGRAM_API_KEY",
        model=os.environ.get("DEEPGRAM_MODEL", "nova-3"),
        language=os.environ.get("DEEPGRAM_LANGUAGE", "zh-CN"),
        endpointing_ms=300,
    )
    provider = DeepgramProvider(config)
    ticket = "live-check-ticket"
    await provider.connect(ticket)

    pcm = make_pcm()
    segment = SpeechSegment(
        segment_id="seg_live_check",
        source="loopback",
        start_ts_ns=0,
        end_ts_ns=len(pcm) // 2 * 1_000_000 // 16000,
        frames=len(pcm) // 640,
        pcm16=pcm,
        sample_rate=16000,
        channels=1,
        peak_dbfs=-12.0,
        rms_dbfs=-15.0,
    )

    started = time.monotonic()
    events: list[dict[str, object]] = []
    first_partial_ms: float | None = None
    final_ms: float | None = None
    try:
        async for result in provider.transcribe(segment):
            elapsed = round((time.monotonic() - started) * 1000, 1)
            if not result.final and first_partial_ms is None:
                first_partial_ms = elapsed
            if result.final:
                final_ms = elapsed
            events.append(
                {
                    "revision": result.revision,
                    "final": result.final,
                    "text": result.text,
                    "ms": elapsed,
                }
            )
    finally:
        await provider.close()

    return {
        "result": "PASS" if events else "FAIL",
        "reason": "ok" if events else "no_results",
        "model": config.model,
        "language": config.language,
        "events": events,
        "firstPartialMs": first_partial_ms,
        "finalMs": final_ms,
    }


def main() -> int:
    if not os.environ.get("DEEPGRAM_API_KEY"):
        print(
            json.dumps(
                {
                    "result": "SKIP",
                    "reason": "DEEPGRAM_API_KEY is not set",
                    "model": os.environ.get("DEEPGRAM_MODEL", "nova-3"),
                }
            )
        )
        return 0
    try:
        report = asyncio.run(run())
    except Exception as error:  # 真实网络/认证失败如实报告
        print(json.dumps({"result": "FAIL", "reason": type(error).__name__, "detail": str(error)}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
