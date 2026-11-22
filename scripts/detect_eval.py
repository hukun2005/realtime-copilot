"""P4.6：人工标注集上的问题检测评测。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from copilot_core.detection import QuestionEngine

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "tests" / "fixtures" / "detect" / "labeled.jsonl"
DEFAULT_OUT = ROOT / "evaluation" / "reports" / "detect-eval.json"


def evaluate(rows: list[dict], *, threshold: float = 0.5) -> dict:
    engine = QuestionEngine(generation=1)
    tp = fp = fn = tn = 0
    type_correct = type_total = 0
    parent_correct = parent_total = 0
    false_positives = []
    false_negatives = []
    type_errors = []

    for index, row in enumerate(rows):
        question, detection = engine.ingest_final(
            segment_id=f"eval_{index}",
            source="loopback",
            text=row["text"],
        )
        predicted = bool(detection and detection.is_question and detection.confidence >= threshold)
        expected = bool(row["expected_is_question"])
        if predicted and expected:
            tp += 1
        elif predicted and not expected:
            fp += 1
            false_positives.append({"text": row["text"], "type": detection.question_type.value, "confidence": detection.confidence})
        elif not predicted and expected:
            fn += 1
            false_negatives.append({"text": row["text"], "expectedType": row["expected_type"]})
        else:
            tn += 1

        if expected:
            type_total += 1
            if predicted and detection.question_type.value == row["expected_type"]:
                type_correct += 1
            elif predicted:
                type_errors.append({"text": row["text"], "expected": row["expected_type"], "actual": detection.question_type.value})
        if row.get("expected_parent"):
            parent_total += 1
            if question is not None and question.parent_id is not None:
                parent_correct += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    statement_total = sum(1 for row in rows if row["expected_type"] == "statement")
    false_positive_texts = {item["text"] for item in false_positives}
    statement_fp = sum(
        1 for row in rows if row["expected_type"] == "statement" and row["text"] in false_positive_texts
    )
    return {
        "samples": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "typeAccuracy": round(type_correct / type_total, 4) if type_total else 0.0,
        "followUpAccuracy": round(parent_correct / parent_total, 4) if parent_total else 0.0,
        "statementFalsePositiveRate": round(statement_fp / statement_total, 4) if statement_total else 0.0,
        "falsePositives": false_positives,
        "falseNegatives": false_negatives,
        "typeErrors": type_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = evaluate(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    ok = (
        result["precision"] >= 0.85
        and result["recall"] >= 0.80
        and result["f1"] >= 0.82
        and result["statementFalsePositiveRate"] <= 0.05
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
