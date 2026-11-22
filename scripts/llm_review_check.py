"""P4 可选：真实 DeepSeek Review / Answer 冒烟（需 DEEPSEEK_API_KEY）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from copilot_core.detection import QuestionEngine, apply_review
from copilot_core.detection.llm import (
    DeepSeekAnswerProvider,
    DeepSeekClient,
    DeepSeekReviewer,
    LlmNotConfigured,
)


async def run(text: str) -> int:
    try:
        client = DeepSeekClient()
    except LlmNotConfigured:
        print(json.dumps({"status": "NOT_RUN", "reason": "DEEPSEEK_API_KEY not set"}, ensure_ascii=False))
        return 2
    try:
        engine = QuestionEngine(generation=1)
        question, detection = engine.ingest_final(segment_id="llm_check", source="loopback", text=text)
        if detection is None:
            print(json.dumps({"status": "ERROR", "reason": "no detection"}, ensure_ascii=False))
            return 1
        reviewer = DeepSeekReviewer(client)
        decision = await reviewer.review(text=text, detection=detection)
        reviewed = apply_review(detection, decision)
        answer = None
        if question is not None:
            engine.confirm(question.id)
            provider = DeepSeekAnswerProvider(client)
            result = await provider.answer(question=question, context=engine.store.history())
            answer = {"text": result.text, "provider": result.provider, "latencyMs": result.latency_ms}
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "rule": {
                        "isQuestion": detection.is_question,
                        "type": detection.question_type.value,
                        "confidence": detection.confidence,
                    },
                    "review": None
                    if decision is None
                    else {
                        "isQuestion": decision.is_question,
                        "type": decision.question_type.value if decision.question_type else None,
                        "confidence": decision.confidence,
                        "reason": decision.reason,
                    },
                    "reviewed": {
                        "isQuestion": reviewed.is_question,
                        "type": reviewed.question_type.value,
                        "confidence": reviewed.confidence,
                    },
                    "answer": answer,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="什么是 Vue3 的响应式原理？")
    args = parser.parse_args()
    return asyncio.run(run(args.text))


if __name__ == "__main__":
    sys.exit(main())
