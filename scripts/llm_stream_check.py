"""P5：真实 DeepSeek 流式冒烟（需 DEEPSEEK_API_KEY）。

不是质量评测；只验证 SSE 分块、完成与主动取消。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from copilot_core.detection import (
    AnswerContext,
    AnswerTaskManager,
    DeepSeekAnswerProvider,
    DeepSeekClient,
    LlmNotConfigured,
    QuestionEngine,
)


async def run(text: str) -> int:
    try:
        client = DeepSeekClient()
    except LlmNotConfigured:
        print(json.dumps({"status": "NOT_RUN", "reason": "DEEPSEEK_API_KEY not set"}, ensure_ascii=False))
        return 2
    try:
        engine = QuestionEngine(generation=1)
        question, _ = engine.ingest_final(segment_id="stream", source="loopback", text=text)
        if question is None:
            print(json.dumps({"status": "ERROR", "reason": "not detected"}, ensure_ascii=False))
            return 1
        context = AnswerContext(
            question_id=question.id,
            question_text=question.text,
            question_type=question.question_type,
        )
        provider = DeepSeekAnswerProvider(client)
        started = time.monotonic()
        chunks: list[str] = []
        async for chunk in provider.stream(question=question, context=context):
            chunks.append(chunk)
        elapsed = round((time.monotonic() - started) * 1000, 1)

        manager = AnswerTaskManager()
        cancelled = False
        seen: list[str] = []

        async def on_delta(chunk: str) -> None:
            nonlocal cancelled
            seen.append(chunk)
            if not cancelled:
                cancelled = True
                await manager.cancel()

        await manager.start(
            provider=provider,
            question=question,
            context=context,
            on_delta=on_delta,
            on_done=lambda _text: _noop(),
            on_error=lambda _code: _noop(),
        )
        for _ in range(200):
            if not manager.running:
                break
            await asyncio.sleep(0.01)

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "model": client.model,
                    "chunks": len(chunks),
                    "chars": len("".join(chunks)),
                    "totalMs": elapsed,
                    "cancelAfterChunks": len(seen),
                    "cancelledRunning": not manager.running,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        await client.close()


async def _noop() -> None:
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="什么是 Vue3 的响应式原理？")
    args = parser.parse_args()
    return asyncio.run(run(args.text))


if __name__ == "__main__":
    raise SystemExit(main())
