"""阿里云 Paraformer 真实识别自检（需 DASHSCOPE_API_KEY）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path

from copilot_core.asr import AsrProviderConfig, DashScopeParaformerProvider, SpeechSegment


async def run(wav_path: Path) -> dict[str, object]:
    with wave.open(str(wav_path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.getnframes()
        pcm = handle.readframes(frames)

    if channels != 1 or width != 2 or rate != 16000:
        raise ValueError(f"expected 16kHz mono PCM16 WAV, got {rate}Hz/{channels}ch/{width * 8}bit")

    config = AsrProviderConfig(
        name="aliyun",
        api_key_env="DASHSCOPE_API_KEY",
        workspace_id=os.environ.get("DASHSCOPE_WORKSPACE_ID", ""),
        model=os.environ.get("RTC_ASR_MODEL", "paraformer-realtime-v2"),
    )
    provider = DashScopeParaformerProvider(config)
    started = time.monotonic()
    await provider.connect("live-check")
    connect_ms = round((time.monotonic() - started) * 1000, 1)

    segment = SpeechSegment(
        segment_id="seg_live_check",
        source="loopback",
        start_ts_ns=0,
        end_ts_ns=int(frames / rate * 1_000_000_000),
        frames=frames // 320,
        pcm16=pcm,
        sample_rate=rate,
        channels=channels,
        peak_dbfs=-12.0,
        rms_dbfs=-20.0,
    )
    started = time.monotonic()
    results: list[dict[str, object]] = []
    async for result in provider.transcribe(segment):
        results.append(
            {
                "ms": round((time.monotonic() - started) * 1000, 1),
                "revision": result.revision,
                "final": result.final,
                "text": result.text,
            }
        )
    metrics = provider.metrics()
    await provider.close()

    finals = [item for item in results if item["final"]]
    return {
        "result": "PASS" if finals else "FAIL",
        "reason": "ok" if finals else "no_final_result",
        "connectMs": connect_ms,
        "audioSeconds": round(frames / rate, 2),
        "partials": len(results) - len(finals),
        "finals": len(finals),
        "results": results,
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, default=Path("artifacts/tts-zh.wav"))
    args = parser.parse_args()
    if not os.environ.get("DASHSCOPE_API_KEY"):
        print(json.dumps({"result": "SKIP", "reason": "DASHSCOPE_API_KEY is not set"}))
        return 0
    try:
        report = asyncio.run(run(args.wav))
    except Exception as error:
        print(json.dumps({"result": "FAIL", "reason": type(error).__name__, "detail": str(error)}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
