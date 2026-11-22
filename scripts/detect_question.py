"""P4.5：手动输入，走与真实 ASR Final 相同的检测入口。"""

from __future__ import annotations

import argparse
import json
import sys

from copilot_core.detection import QuestionEngine


def format_result(question, detection) -> dict:
    return {
        "text": detection.text if detection else None,
        "isQuestion": detection.is_question if detection else False,
        "type": detection.question_type.value if detection else None,
        "confidence": detection.confidence if detection else None,
        "reason": detection.reason if detection else None,
        "questionId": question.id if question else None,
        "state": question.state.value if question else None,
        "parentId": question.parent_id if question else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", action="append", help="可重复；省略则逐行读 stdin")
    parser.add_argument("--source", default="loopback")
    args = parser.parse_args()

    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    engine = QuestionEngine(generation=1)
    lines = args.text if args.text else [line.strip() for line in sys.stdin if line.strip()]
    for line in lines:
        question, detection = engine.submit_text(line, source=args.source)
        print(json.dumps(format_result(question, detection), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
