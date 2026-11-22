"""P4 预留的问答接口（不包含在线 LLM 调用）。

只定义 CONFIRMED → ANSWERING → ANSWERED/FAILED 的调用边界。
P4 不接 DeepSeek、不生成真实答案；实现者由后续阶段注入。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .state import Question


@dataclass(frozen=True)
class AnswerResult:
    text: str
    provider: str
    latency_ms: float


class AnswerProvider(Protocol):
    async def answer(self, *, question: Question, context: Sequence[Question]) -> AnswerResult: ...


class UnavailableAnswerProvider:
    """默认实现：明确失败，不伪造答案。"""

    async def answer(self, *, question: Question, context: Sequence[Question]) -> AnswerResult:
        raise RuntimeError("answer provider is not configured")


async def answer_question(
    *,
    question: Question,
    provider: AnswerProvider,
    context: Sequence[Question] = (),
) -> AnswerResult:
    """执行状态迁移并调用 Provider；失败进入 FAILED。"""
    from .state import QuestionState

    if question.state is not QuestionState.CONFIRMED:
        raise ValueError(f"question must be confirmed, got {question.state}")
    question.transition(QuestionState.ANSWERING)
    started = time.monotonic()
    try:
        result = await provider.answer(question=question, context=context)
    except Exception:
        question.transition(QuestionState.FAILED)
        raise
    question.transition(QuestionState.ANSWERED)
    return AnswerResult(
        text=result.text,
        provider=result.provider,
        latency_ms=result.latency_ms or round((time.monotonic() - started) * 1000, 1),
    )
