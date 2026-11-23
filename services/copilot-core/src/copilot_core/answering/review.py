"""P51-03：结构化 Review 与降级策略。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..detection import Detection, Question, QuestionType

ReviewCall = Callable[[str, Detection, tuple[str, ...]], Awaitable[dict[str, object] | str]]


class ReviewStatus(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"


class ReviewOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewStatus
    confidence: float = Field(ge=0.0, le=1.0)
    question_type: QuestionType
    reason: str
    normalized_question: str
    review_status: str = "ok"


class ReviewService:
    """Review 失败只降级，不把故障变成空答案。"""

    def __init__(
        self,
        *,
        call: ReviewCall | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self._call = call
        self.timeout_seconds = timeout_seconds

    async def review(
        self,
        *,
        question: Question,
        detection: Detection,
        recent: tuple[str, ...] = (),
    ) -> ReviewOutcome:
        if self._call is None:
            return _fallback(detection, "review_not_configured")
        try:
            raw = await asyncio.wait_for(
                self._call(question.text, detection, recent),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            return _fallback(detection, "review_timeout")
        except Exception:
            return _fallback(detection, "review_error")
        return _parse(raw, detection)

    def should_answer(self, outcome: ReviewOutcome, detection: Detection) -> bool:
        if outcome.decision is ReviewStatus.CONFIRMED:
            return True
        if outcome.decision is ReviewStatus.REJECTED:
            return False
        if outcome.decision is ReviewStatus.UNCERTAIN:
            return detection.is_question and detection.confidence >= 0.55
        # unavailable: use rule result, never turn failure into a blank answer
        return detection.is_question


def _parse(raw: dict[str, object] | str, detection: Detection) -> ReviewOutcome:
    try:
        if isinstance(raw, str):
            return ReviewOutcome.model_validate_json(raw)
        return ReviewOutcome.model_validate(raw)
    except (ValidationError, ValueError):
        return _fallback(detection, "review_invalid")


def _fallback(detection: Detection, reason: str) -> ReviewOutcome:
    return ReviewOutcome(
        decision=ReviewStatus.UNAVAILABLE,
        confidence=detection.confidence,
        question_type=detection.question_type,
        reason=reason,
        normalized_question=detection.normalized_text,
        review_status="unavailable",
    )


def deepseek_review_call(
    complete: Callable[..., Awaitable[str]],
) -> ReviewCall:
    """把现有 DeepSeekClient.complete 包装成 Review 调用；Key 不进 Prompt/日志。"""

    async def call(text: str, detection: Detection, recent: tuple[str, ...]) -> str:
        prompt = (
            "只输出 JSON：{\"decision\":\"confirmed|rejected|uncertain\","
            "\"confidence\":0.0-1.0,\"question_type\":\"technical|project|behavioral|scenario|follow_up|general|statement\","
            "\"reason\":\"short\",\"normalized_question\":\"text\"}\n"
            f"规则: {detection.question_type.value}, {detection.confidence}\n"
            f"最近: {' | '.join(recent[-3:])}\n文本: {text}"
        )
        return await complete(
            [{"role": "system", "content": "只输出 JSON。"}, {"role": "user", "content": prompt}],
            max_tokens=160,
        )

    return call
