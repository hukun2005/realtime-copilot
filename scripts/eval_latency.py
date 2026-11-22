"""P35-04：Segment 级 ASR 延迟评测。

测量口径（每样本）：

- t0：调用 ``provider.transcribe(segment)`` 前；
- 首次 partial：第一个非 final 结果到达；
- final：第一个 final 结果到达。

这是 Segment 识别延迟，不是“面试回答延迟”，也不是端到端字幕延迟。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copilot_core.asr import AsrRegistry
from copilot_core.evaluation import LatencyRecord, summarize

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from eval_providers import load_samples, load_segment, provider_config  # noqa: E402

ROOT = SCRIPTS.parent
REPORTS = ROOT / "evaluation" / "reports"


async def measure(provider: Any, samples: list[dict[str, Any]]) -> tuple[list[LatencyRecord], list[str]]:
    records: list[LatencyRecord] = []
    errors: list[str] = []
    await provider.connect("latency-eval")
    try:
        for sample in samples:
            segment = load_segment(sample)
            started = time.monotonic()
            first_partial = None
            first_final = None
            finals = 0
            error = None
            try:
                async for result in provider.transcribe(segment):
                    elapsed = (time.monotonic() - started) * 1000
                    if result.final:
                        finals += 1
                        if first_final is None:
                            first_final = elapsed
                    elif first_partial is None:
                        first_partial = elapsed
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                errors.append(f"{sample['id']}: {error}")
            upload_estimate = float(sample["durationSeconds"]) * 100.0
            records.append(
                LatencyRecord(
                    sample_id=sample["id"],
                    category=sample["category"],
                    audio_seconds=float(sample["durationSeconds"]),
                    first_partial_ms=round(first_partial, 1) if first_partial is not None else None,
                    final_ms=round(first_final, 1) if first_final is not None else None,
                    finals=finals,
                    finalization_ms=(
                        round(first_final - upload_estimate, 1)
                        if first_final is not None
                        else None
                    ),
                    error=error,
                )
            )
    finally:
        await provider.close()
    return records, errors


async def run_provider(name: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    config = provider_config(name)
    if config is None:
        key = "DASHSCOPE_API_KEY" if name == "aliyun" else "DEEPGRAM_API_KEY"
        return {"provider": name, "status": "NOT_RUN", "reason": f"{key} not set"}
    try:
        provider = AsrRegistry.create(config)
        records, errors = await measure(provider, samples)
    except Exception as exc:
        return {"provider": name, "status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"}
    summary = summarize(records)
    status = "PASS" if not errors and summary["withFinal"] > 0 else "PARTIAL"
    return {
        "provider": name,
        "status": status,
        "model": getattr(config, "model", None),
        "summary": summary,
        "errors": errors,
        "samples": [
            {
                "id": item.sample_id,
                "category": item.category,
                "audioSeconds": item.audio_seconds,
                "firstPartialMs": item.first_partial_ms,
                "finalMs": item.final_ms,
                "finalizationMs": item.finalization_ms,
                "uploadEstimateMs": round(item.audio_seconds * 100.0, 1),
                "finals": item.finals,
                "error": item.error,
            }
            for item in records
        ],
    }


def _fmt(value: Any) -> str:
    return f"{value}" if value is not None else "-"


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# P3.5 ASR Segment 延迟（合成 TTS）",
        "",
        f"- 生成时间：{report['generatedAt']}",
        f"- 数据集：`{report['dataset']}`",
        "- 口径：从调用 transcribe 到首个 partial/final；不是面试回答延迟",
        "",
        "| Provider | 状态 | 模型 | 有 final | Final P50 | Final P95 | Finalization P50 | Finalization P95 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in report["providers"]:
        summary = item.get("summary") or {}
        lines.append(
            f"| {item['provider']} | {item['status']} | {item.get('model') or '-'} | "
            f"{summary.get('withFinal', '-')} | {_fmt(summary.get('finalP50Ms'))} | "
            f"{_fmt(summary.get('finalP95Ms'))} | {_fmt(summary.get('finalizationP50Ms'))} | "
            f"{_fmt(summary.get('finalizationP95Ms'))} |"
        )
    for item in report["providers"]:
        if item["status"] in ("NOT_RUN", "ERROR"):
            lines += ["", f"- {item['provider']}：{item['status']}（{item.get('reason')}）"]
            continue
        lines += [
            "",
            f"## {item['provider']} 逐样本（毫秒）",
            "",
            "| 样本 | 类别 | 音频秒 | 首 partial | final | finalization | finals | 错误 |",
            "|---|---|---|---|---|---|---|",
        ]
        for sample in item["samples"]:
            lines.append(
                f"| {sample['id']} | {sample['category']} | {sample['audioSeconds']:.2f} | "
                f"{_fmt(sample['firstPartialMs'])} | {_fmt(sample['finalMs'])} | "
                f"{_fmt(sample['finalizationMs'])} | {sample['finals']} | {sample['error'] or '-'} |"
            )
    lines += [
        "",
        "> Final 从提交整段音频算起；Finalization 减去上传耗时估计（100ms 音频/10ms 间隔 ≈ 10x 实时）。",
        "> 样本量很小（<=11），P95 接近最大值；不得把 TTS 延迟当作真实会议/面试回答延迟。",
        "",
    ]
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--providers", nargs="+", default=["aliyun", "deepgram"])
    args = parser.parse_args()
    samples = load_samples()
    providers = [await run_provider(name, samples) for name in args.providers]
    report = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "dataset": "realtime-copilot-p35-synthetic",
        "providers": providers,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS / "latency-comparison.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (REPORTS / "latency-comparison.md").write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps({"providers": [{"provider": p["provider"], "status": p["status"], "summary": p.get("summary")} for p in providers]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
