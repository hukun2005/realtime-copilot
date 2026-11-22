"""P35-03：对同一评测集运行真实 ASR Provider，生成假设与 CER/WER 对比。

- 不伪造结果：缺 Key/连接失败会标记 NOT_RUN/ERROR，不会写成 PASS。
- 音频只来自 evaluation/dataset（合成 TTS），真实会议录音不参与。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copilot_core.asr import AsrProviderConfig, AsrRegistry, SpeechSegment

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "dataset"
REPORTS = ROOT / "evaluation" / "reports"


def load_samples() -> list[dict[str, Any]]:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    return manifest["samples"]


def load_segment(sample: dict[str, Any]) -> SpeechSegment:
    path = DATASET / sample["file"]
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.getnframes()
        pcm = handle.readframes(frames)
    return SpeechSegment(
        segment_id=f"eval_{sample['id']}",
        source="loopback",
        start_ts_ns=0,
        end_ts_ns=int(frames / rate * 1_000_000_000),
        frames=max(1, len(pcm) // 640),
        pcm16=pcm,
        sample_rate=rate,
        channels=1,
        peak_dbfs=-6.0,
        rms_dbfs=-20.0,
    )


async def transcribe_all(provider: Any, samples: list[dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
    hypotheses: dict[str, str] = {}
    errors: list[str] = []
    await provider.connect("eval-provider")
    try:
        for sample in samples:
            segment = load_segment(sample)
            finals: list[str] = []
            try:
                async for result in provider.transcribe(segment):
                    if result.final:
                        finals.append(result.text)
            except Exception as error:  # 记录后继续下一个样本
                errors.append(f"{sample['id']}: {type(error).__name__}: {error}")
                continue
            hypotheses[sample["id"]] = "".join(finals)
    finally:
        await provider.close()
    return hypotheses, errors


def provider_config(name: str) -> AsrProviderConfig | None:
    if name == "aliyun":
        if not os.environ.get("DASHSCOPE_API_KEY"):
            return None
        return AsrProviderConfig(
            name="aliyun",
            api_key_env="DASHSCOPE_API_KEY",
            workspace_id=os.environ.get("DASHSCOPE_WORKSPACE_ID"),
            model=os.environ.get("RTC_ASR_MODEL") or "paraformer-realtime-v2",
        )
    if name == "deepgram":
        if not os.environ.get("DEEPGRAM_API_KEY"):
            return None
        return AsrProviderConfig(name="deepgram", api_key_env="DEEPGRAM_API_KEY")
    raise ValueError(f"unknown provider: {name}")


async def run_provider(name: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    config = provider_config(name)
    if config is None:
        key = "DASHSCOPE_API_KEY" if name == "aliyun" else "DEEPGRAM_API_KEY"
        return {"provider": name, "status": "NOT_RUN", "reason": f"{key} not set"}
    try:
        provider = AsrRegistry.create(config)
        hypotheses, errors = await transcribe_all(provider, samples)
    except Exception as error:
        return {
            "provider": name,
            "status": "ERROR",
            "reason": f"{type(error).__name__}: {error}",
        }
    path = REPORTS / f"hypotheses-{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hypotheses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status = "PASS" if not errors and len(hypotheses) == len(samples) else "PARTIAL"
    return {
        "provider": name,
        "status": status,
        "hypotheses": len(hypotheses),
        "errors": errors,
        "hypothesesFile": str(path.relative_to(ROOT)),
        "model": getattr(config, "model", None),
    }


def score(name: str) -> dict[str, Any] | None:
    hypotheses = REPORTS / f"hypotheses-{name}.json"
    if not hypotheses.is_file():
        return None
    output = REPORTS / f"cer-wer-{name}.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "evaluate_asr.py"),
            "--hypotheses",
            str(hypotheses),
            "--source",
            name,
            "--label",
            name,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"status": "ERROR", "stderr": completed.stderr.strip()}
    return json.loads(output.read_text(encoding="utf-8"))


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# P3.5 Provider 对比（合成 TTS）",
        "",
        f"- 生成时间：{report['generatedAt']}",
        f"- 数据集：`{report['dataset']}`",
        "- 音频：合成 TTS，不是真实会议；真实会议 WER 仍 `NOT_MEASURED`",
        "",
        "| Provider | 状态 | 模型 | CER micro | WER micro | 计分样本 |",
        "|---|---|---|---|---|---|",
    ]
    for item in report["providers"]:
        score_data = (item.get("score") or {}).get("aggregate") or {}
        lines.append(
            f"| {item['provider']} | {item['status']} | {item.get('model') or '-'} | "
            f"{_pct(score_data.get('cerMicro'))} | {_pct(score_data.get('werMicro'))} | "
            f"{score_data.get('scoredSamples', '-')} |"
        )
    lines += ["", "## 说明", ""]
    for item in report["providers"]:
        if item["status"] == "NOT_RUN":
            lines.append(f"- {item['provider']}：NOT_RUN（{item.get('reason')}）")
        if item["status"] == "ERROR":
            lines.append(f"- {item['provider']}：ERROR（{item.get('reason')}）")
        if item["status"] == "PARTIAL":
            lines.append(f"- {item['provider']}：PARTIAL（{len(item.get('errors', []))} 个样本失败）")
    lines += ["", "> 不得把 NOT_RUN/ERROR/PARTIAL 当作 PASS；也不得把本表当作真实会议识别质量。", ""]
    return "\n".join(lines)


def _pct(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.2%}"
    return "-"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--providers", nargs="+", default=["aliyun", "deepgram"])
    args = parser.parse_args()
    samples = load_samples()

    providers = []
    for name in args.providers:
        result = await run_provider(name, samples)
        if result["status"] in ("PASS", "PARTIAL"):
            result["score"] = score(name)
        providers.append(result)

    report = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "dataset": "realtime-copilot-p35-synthetic",
        "providers": providers,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS / "provider-comparison.json"
    md_path = REPORTS / "provider-comparison.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
