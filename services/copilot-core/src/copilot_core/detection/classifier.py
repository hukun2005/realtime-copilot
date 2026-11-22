"""P4.2：可解释的本地规则问题检测（不接在线 LLM）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .transcript import normalize_text


class QuestionType(StrEnum):
    TECHNICAL = "technical"
    PROJECT = "project"
    BEHAVIORAL = "behavioral"
    SCENARIO = "scenario"
    FOLLOW_UP = "follow_up"
    GENERAL = "general"
    STATEMENT = "statement"


@dataclass(frozen=True)
class Detection:
    is_question: bool
    question_type: QuestionType
    confidence: float
    text: str
    normalized_text: str
    reason: str
    source: str
    segment_id: str


TECHNICAL_TERMS = (
    "闭包", "事件循环", "响应式", "虚拟dom", "原型链", "垃圾回收", "内存泄漏",
    "性能", "缓存", "索引", "事务", "死锁", "线程", "进程", "幂等", "分布式",
    "微服务", "算法", "数据结构", "vue", "react", "typescript", "javascript",
    "python", "sql", "http", "tcp", "api", "pinia", "proxy", "跨域", "鉴权", "dom",
)
PROJECT_TERMS = (
    "项目", "实习", "经历", "负责", "技术选型", "架构", "难点", "挑战", "上线",
    "团队", "需求", "迭代", "业务", "系统设计",
)
BEHAVIOR_TERMS = (
    "冲突", "压力", "沟通", "失败", "缺点", "优点", "职业规划", "离职", "加班",
    "同事", "领导", "复盘", "成长", "学习", "批评", "激励", "冒险", "离开",
)
SCENARIO_MARKERS = ("如果", "假如", "让你", "设计一个", "你会怎么做", "如何设计", "怎么设计")
FOLLOWUP_MARKERS = (
    "那如果", "那", "然后呢", "还有呢", "还有", "具体", "展开", "刚才", "你说的",
    "它和", "这个",
)
QUESTION_MARKERS = ("?", "？", "吗", "呢", "什么", "如何", "怎么", "为什么", "哪", "是否", "能不能", "可不可以")
IMPERATIVE = ("请介绍", "介绍一下", "解释", "说说", "谈谈", "讲一下", "描述", "展开说说", "具体说说")


def normalize_for_detection(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_text(text)).strip()


def _has_any(text: str, terms: tuple[str, ...]) -> str | None:
    for term in terms:
        if term in text:
            return term
    return None


def detect_question(
    *,
    text: str,
    source: str,
    segment_id: str,
    has_context: bool = False,
) -> Detection:
    normalized = normalize_for_detection(text)
    if not normalized:
        return Detection(False, QuestionType.STATEMENT, 0.0, text, normalized, "empty text", source, segment_id)

    question_mark = "?" in text or "？" in text
    marker = _has_any(normalized, QUESTION_MARKERS)
    imperative = _has_any(normalized, IMPERATIVE)
    follow_marker = _has_any(normalized, FOLLOWUP_MARKERS) if has_context else None
    technical = _has_any(normalized, TECHNICAL_TERMS)
    project = _has_any(normalized, PROJECT_TERMS)
    behavioral = _has_any(normalized, BEHAVIOR_TERMS)
    scenario = _has_any(normalized, SCENARIO_MARKERS)

    if question_mark or marker or imperative:
        if follow_marker and has_context:
            qtype = QuestionType.FOLLOW_UP
            reason = f"follow-up marker: {follow_marker}"
        elif scenario:
            qtype = QuestionType.SCENARIO
            reason = f"scenario marker: {scenario}"
        elif behavioral:
            qtype = QuestionType.BEHAVIORAL
            reason = f"behavioral term: {behavioral}"
        elif project:
            qtype = QuestionType.PROJECT
            reason = f"project term: {project}"
        elif technical:
            qtype = QuestionType.TECHNICAL
            reason = f"technical term: {technical}"
        else:
            qtype = QuestionType.GENERAL
            reason = "question marker without domain term"
        confidence = 0.62
        if question_mark:
            confidence += 0.15
        if marker:
            confidence += 0.1
        if imperative:
            confidence += 0.08
        if technical or project or behavioral or scenario:
            confidence += 0.05
        return Detection(
            True,
            qtype,
            round(min(confidence, 0.98), 2),
            text,
            normalized,
            reason,
            source,
            segment_id,
        )

    return Detection(
        False,
        QuestionType.STATEMENT,
        0.15,
        text,
        normalized,
        "no question marker or interrogative",
        source,
        segment_id,
    )
