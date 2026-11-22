"""P4 预留的 LLM Review 接口（不包含在线调用）。

规则检测仍是默认路径；调用方可以注入 Reviewer 做二次复核。
Review 结果只能修正检测字段，不能绕过 QuestionEngine 的入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .classifier import Detection, QuestionType


@dataclass(frozen=True)
class ReviewDecision:
    is_question: bool | None = None
    question_type: QuestionType | None = None
    confidence: float | None = None
    reason: str | None = None


class QuestionReviewer(Protocol):
    """可插拔复核器；真实 LLM 实现不在 P4 范围内。"""

    def review(self, *, text: str, detection: Detection) -> ReviewDecision | None: ...


class NoopReviewer:
    def review(self, *, text: str, detection: Detection) -> ReviewDecision | None:
        return None


def apply_review(detection: Detection, decision: ReviewDecision | None) -> Detection:
    if decision is None:
        return detection
    is_question = detection.is_question if decision.is_question is None else decision.is_question
    question_type = detection.question_type if decision.question_type is None else decision.question_type
    confidence = detection.confidence if decision.confidence is None else max(0.0, min(1.0, decision.confidence))
    reason = detection.reason
    if decision.reason:
        reason = f"{reason} | review: {decision.reason}"
    return Detection(
        is_question=is_question,
        question_type=question_type,
        confidence=round(confidence, 2),
        text=detection.text,
        normalized_text=detection.normalized_text,
        reason=reason,
        source=detection.source,
        segment_id=detection.segment_id,
    )
