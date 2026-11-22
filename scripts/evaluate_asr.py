"""对 ASR 假设计算 CER/WER，输出 JSON + Markdown 报告。

假设来源由调用方提供（JSON: `{sampleId: text}`）；本工具不调用任何 Provider。
默认不把参考/假设正文写入报告，避免真实会议内容外泄。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from copilot_core.evaluation import aggregate, score_sample

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "dataset"


def load_manifest(dataset: Path) -> dict:
    path = dataset / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def verify_hashes(dataset: Path, manifest: dict) -> list[str]:
    errors = []
    for sample in manifest["samples"]:
        path = dataset / sample["file"]
        if not path.is_file():
            errors.append(f"missing {sample['file']}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != sample["sha256"]:
            errors.append(f"hash mismatch {sample['file']}")
    return errors


def build_report(
    manifest: dict,
    hypotheses: dict[str, str],
    *,
    source: str,
    label: str,
) -> dict:
    scores = []
    missing = []
    for sample in manifest["samples"]:
        sample_id = sample["id"]
        if sample_id not in hypotheses:
            missing.append(sample_id)
            continue
        scores.append(
            score_sample(
                sample_id=sample_id,
                category=sample["category"],
                reference=sample["reference"],
                hypothesis=hypotheses[sample_id],
            )
        )
    result = aggregate(scores)
    return {
        "dataset": manifest["name"],
        "datasetVersion": manifest["version"],
        "label": label,
        "hypothesisSource": source,
        "generatedAt": datetime.now(UTC).isoformat(),
        "missing": missing,
        "aggregate": {
            "sampleCount": result.sample_count,
            "scoredSamples": result.scored_samples,
            "emptyReferenceSamples": result.empty_reference_samples,
            "hallucinations": result.hallucinations,
            "cerMicro": result.cer_micro,
            "werMicro": result.wer_micro,
            "cerMacro": result.cer_macro,
            "werMacro": result.wer_macro,
            "totalCerErrors": result.total_cer_errors,
            "totalCerReference": result.total_cer_reference,
            "totalWerErrors": result.total_wer_errors,
            "totalWerReference": result.total_wer_reference,
            "perCategory": result.per_category,
        },
        "samples": [
            {
                "id": item.sample_id,
                "category": item.category,
                "cer": round(item.cer, 4),
                "wer": round(item.wer, 4),
                "cerErrors": item.cer_counts.errors,
                "cerReference": item.cer_counts.reference_length,
                "hallucination": item.hallucination,
            }
            for item in scores
        ],
    }


def to_markdown(report: dict) -> str:
    aggregate_data = report["aggregate"]
    lines = [
        f"# ASR CER/WER：{report['label']}",
        "",
        f"- 数据集：`{report['dataset']}` v{report['datasetVersion']}",
        f"- 假设来源：{report['hypothesisSource']}",
        f"- 生成时间：{report['generatedAt']}",
        f"- 缺失样本：{len(report['missing'])}",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| CER micro | {aggregate_data['cerMicro']:.2%} |",
        f"| CER macro | {aggregate_data['cerMacro']:.2%} |",
        f"| WER micro | {aggregate_data['werMicro']:.2%} |",
        f"| WER macro | {aggregate_data['werMacro']:.2%} |",
        f"| 计分样本 | {aggregate_data['scoredSamples']} / {aggregate_data['sampleCount']} |",
        f"| 静音幻觉 | {aggregate_data['hallucinations']} |",
        "",
        "## 分类",
        "",
        "| 类别 | 样本 | CER micro | CER macro |",
        "|---|---|---|---|",
    ]
    for category, data in aggregate_data["perCategory"].items():
        lines.append(
            f"| {category} | {data['samples']} | {data['cerMicro']:.2%} | {data['cerMacro']:.2%} |"
        )
    lines += [
        "",
        "## 逐样本",
        "",
        "| ID | 类别 | CER | WER | 错误/参考 | 幻觉 |",
        "|---|---|---|---|---|---|",
    ]
    for sample in report["samples"]:
        lines.append(
            f"| {sample['id']} | {sample['category']} | {sample['cer']:.2%} | "
            f"{sample['wer']:.2%} | {sample['cerErrors']}/{sample['cerReference']} | "
            f"{'是' if sample['hallucination'] else '否'} |"
        )
    lines += [
        "",
        "> CER 以字符计，WER 以 Latin 词/CJK 字计；静音样本单独统计幻觉，不计入 CER/WER。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--hypotheses", type=Path)
    parser.add_argument("--oracle", action="store_true", help="用参考文本自检工具（不是 ASR 结果）")
    parser.add_argument("--label", default="tool")
    parser.add_argument("--source", default="unknown")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest(args.dataset)
    hash_errors = verify_hashes(args.dataset, manifest)
    if hash_errors:
        for error in hash_errors:
            print(f"dataset error: {error}", file=sys.stderr)
        return 2

    if args.oracle:
        hypotheses = {sample["id"]: sample["reference"] for sample in manifest["samples"]}
        source = "oracle (self-check, not ASR)"
    elif args.hypotheses:
        hypotheses = json.loads(args.hypotheses.read_text(encoding="utf-8"))
        source = args.source if args.source != "unknown" else str(args.hypotheses)
    else:
        parser.error("provide --hypotheses or --oracle")

    report = build_report(manifest, hypotheses, source=source, label=args.label)
    if report["missing"] and not args.allow_missing:
        print(f"missing hypotheses: {', '.join(report['missing'])}", file=sys.stderr)
        return 3

    output = args.output or ROOT / "evaluation" / "reports" / f"cer-wer-{args.label}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps(report["aggregate"], ensure_ascii=False))
    print(f"report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
