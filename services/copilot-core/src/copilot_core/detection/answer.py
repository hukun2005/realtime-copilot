"""P4 预留的问答接口（不包含在线 LLM 调用）。

只定义 CONFIRMED → ANSWERING → ANSWERED/FAILED 的调用边界。
P4 不接 DeepSeek、不生成真实答案；实现者由后续阶段注入。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .classifier import QuestionType
from .state import Question


@dataclass(frozen=True)
class AnswerContext:
    """回答上下文；项目证据与通用知识严格分开。"""

    question_id: str
    question_text: str
    question_type: QuestionType
    parent_question: str | None = None
    recent_questions: tuple[str, ...] = ()
    resume_summary: str | None = None
    selected_projects: tuple[str, ...] = ()
    project_evidence: tuple[str, ...] = ()
    technical_knowledge: tuple[str, ...] = ()
    answer_style: str = "口语化、简洁、3-6 句"
    token_budget: int = 1200


@dataclass(frozen=True)
class AnswerResult:
    text: str
    provider: str
    latency_ms: float


class AnswerProvider(Protocol):
    async def answer(self, *, question: Question, context: Sequence[Question]) -> AnswerResult: ...


def build_answer_context(
    question: Question,
    *,
    parent: Question | None = None,
    recent: Sequence[Question] = (),
    resume_summary: str | None = None,
    selected_projects: Sequence[str] = (),
    project_evidence: Sequence[str] = (),
    technical_knowledge: Sequence[str] = (),
    token_budget: int = 1200,
) -> AnswerContext:
    return AnswerContext(
        question_id=question.id,
        question_text=question.text,
        question_type=question.question_type,
        parent_question=parent.text if parent else None,
        recent_questions=tuple(item.text for item in recent[-5:]),
        resume_summary=resume_summary,
        selected_projects=tuple(selected_projects),
        project_evidence=tuple(project_evidence),
        technical_knowledge=tuple(technical_knowledge),
        token_budget=token_budget,
    )


def build_answer_messages(context: AnswerContext) -> list[dict[str, str]]:
    system = (
        "你是面试回答助手。用自然口语中文，先直接回答问题，再给必要技术解释。"
        "项目问题只依据提供的项目证据；没有证据就说明不确定，不编造项目功能。"
        "简单问题 3-6 句，避免念稿感。"
    )
    project = "\n".join(f"- {item}" for item in context.project_evidence) or "(无项目证据，不要编造具体项目细节)"
    technical = "\n".join(f"- {item}" for item in context.technical_knowledge) or "(无)"
    recent = "\n".join(f"- {item}" for item in context.recent_questions) or "(无)"
    user = (
        f"问题：{context.question_text}\n"
        f"类型：{context.question_type.value}\n"
        f"上一问：{context.parent_question or '(无)'}\n"
        f"最近问题：\n{recent}\n"
        f"简历摘要：{context.resume_summary or '(无)'}\n"
        f"已选项目：{', '.join(context.selected_projects) or '(无)'}\n"
        f"项目证据：\n{project}\n"
        f"通用技术知识：\n{technical}\n"
        f"回答风格：{context.answer_style}；token 预算：{context.token_budget}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


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
