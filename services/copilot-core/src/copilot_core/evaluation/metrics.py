"""ASR 评测指标：字符错误率（CER）与词错误率（WER）。

口径：

- 文本先做 NFKC、小写、去标点/符号/控制字符。
- CER 以字符为单位（空白不计）；WER 以 Latin 词 + 单个 CJK 字符为单位。
- 空参考：hypothesis 为空记 0 错误，否则记 1 次幻觉；空参考样本不进入
  CER/WER 的 micro/macro 汇总，单独报告。
- 汇总同时给 micro（总错误/总参考单元）与 macro（逐样本平均）。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EditCounts:
    substitutions: int
    insertions: int
    deletions: int
    reference_length: int
    hypothesis_length: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.insertions + self.deletions

    @property
    def rate(self) -> float:
        if self.reference_length == 0:
            return 0.0 if self.hypothesis_length == 0 else 1.0
        return self.errors / self.reference_length


@dataclass(frozen=True)
class SampleScore:
    sample_id: str
    category: str
    cer: float
    wer: float
    cer_counts: EditCounts
    wer_counts: EditCounts
    reference: str
    hypothesis: str
    hallucination: bool = False


@dataclass
class AggregateScore:
    sample_count: int
    scored_samples: int
    empty_reference_samples: int
    hallucinations: int
    cer_micro: float
    wer_micro: float
    cer_macro: float
    wer_macro: float
    total_cer_errors: int = 0
    total_cer_reference: int = 0
    total_wer_errors: int = 0
    total_wer_reference: int = 0
    per_category: dict[str, dict[str, float]] = field(default_factory=dict)


def normalize_text(text: str) -> str:
    """NFKC + 小写 + 去标点/符号/控制字符，保留字母、数字、CJK 与空白。"""
    normalized = unicodedata.normalize("NFKC", text).lower()
    kept = []
    for char in normalized:
        if char.isspace():
            kept.append(" ")
            continue
        category = unicodedata.category(char)
        if category[0] in ("P", "S", "C", "Z", "M"):
            continue
        kept.append(char)
    return "".join(kept).strip()


def cer_units(text: str) -> list[str]:
    """CER 单元：去除空白后的字符序列。"""
    return [char for char in normalize_text(text) if not char.isspace()]


def wer_units(text: str) -> list[str]:
    """WER 单元：Latin/数字按词，CJK 按单字。"""
    units: list[str] = []
    word: list[str] = []
    for char in normalize_text(text):
        if "\u4e00" <= char <= "\u9fff" or "\u3400" <= char <= "\u4dbf":
            if word:
                units.append("".join(word))
                word.clear()
            units.append(char)
        elif char.isalnum():
            word.append(char)
        else:
            if word:
                units.append("".join(word))
                word.clear()
    if word:
        units.append("".join(word))
    return units


def edit_counts(reference: Sequence[str], hypothesis: Sequence[str]) -> EditCounts:
    """Levenshtein 对齐并回填替换/插入/删除计数。"""
    rows = len(reference) + 1
    columns = len(hypothesis) + 1
    cost = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        cost[row][0] = row
    for column in range(columns):
        cost[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            substitution = cost[row - 1][column - 1] + (reference[row - 1] != hypothesis[column - 1])
            deletion = cost[row - 1][column] + 1
            insertion = cost[row][column - 1] + 1
            cost[row][column] = min(substitution, deletion, insertion)

    substitutions = insertions = deletions = 0
    row, column = len(reference), len(hypothesis)
    while row > 0 or column > 0:
        if (
            row > 0
            and column > 0
            and reference[row - 1] == hypothesis[column - 1]
            and cost[row][column] == cost[row - 1][column - 1]
        ):
            row -= 1
            column -= 1
        elif row > 0 and column > 0 and cost[row][column] == cost[row - 1][column - 1] + 1:
            substitutions += 1
            row -= 1
            column -= 1
        elif row > 0 and cost[row][column] == cost[row - 1][column] + 1:
            deletions += 1
            row -= 1
        else:
            insertions += 1
            column -= 1

    return EditCounts(
        substitutions=substitutions,
        insertions=insertions,
        deletions=deletions,
        reference_length=len(reference),
        hypothesis_length=len(hypothesis),
    )


def score_sample(
    *,
    sample_id: str,
    category: str,
    reference: str,
    hypothesis: str,
) -> SampleScore:
    if not normalize_text(reference):
        hallucination = bool(normalize_text(hypothesis))
        return SampleScore(
            sample_id=sample_id,
            category=category,
            cer=1.0 if hallucination else 0.0,
            wer=1.0 if hallucination else 0.0,
            cer_counts=EditCounts(0, 0, 0, 0, len(cer_units(hypothesis))),
            wer_counts=EditCounts(0, 0, 0, 0, len(wer_units(hypothesis))),
            reference=reference,
            hypothesis=hypothesis,
            hallucination=hallucination,
        )
    cer = edit_counts(cer_units(reference), cer_units(hypothesis))
    wer = edit_counts(wer_units(reference), wer_units(hypothesis))
    return SampleScore(
        sample_id=sample_id,
        category=category,
        cer=cer.rate,
        wer=wer.rate,
        cer_counts=cer,
        wer_counts=wer,
        reference=reference,
        hypothesis=hypothesis,
    )


def aggregate(scores: Iterable[SampleScore]) -> AggregateScore:
    items = list(scores)
    scored = [item for item in items if normalize_text(item.reference)]
    empty = [item for item in items if not normalize_text(item.reference)]
    total_cer_errors = sum(item.cer_counts.errors for item in scored)
    total_cer_reference = sum(item.cer_counts.reference_length for item in scored)
    total_wer_errors = sum(item.wer_counts.errors for item in scored)
    total_wer_reference = sum(item.wer_counts.reference_length for item in scored)

    categories: dict[str, dict[str, float]] = {}
    for category in sorted({item.category for item in scored}):
        subset = [item for item in scored if item.category == category]
        errors = sum(item.cer_counts.errors for item in subset)
        reference = sum(item.cer_counts.reference_length for item in subset)
        categories[category] = {
            "samples": len(subset),
            "cerMicro": round(errors / reference, 4) if reference else 0.0,
            "cerMacro": round(sum(item.cer for item in subset) / len(subset), 4),
        }

    return AggregateScore(
        sample_count=len(items),
        scored_samples=len(scored),
        empty_reference_samples=len(empty),
        hallucinations=sum(1 for item in empty if item.hallucination),
        cer_micro=round(total_cer_errors / total_cer_reference, 4) if total_cer_reference else 0.0,
        wer_micro=round(total_wer_errors / total_wer_reference, 4) if total_wer_reference else 0.0,
        cer_macro=round(sum(item.cer for item in scored) / len(scored), 4) if scored else 0.0,
        wer_macro=round(sum(item.wer for item in scored) / len(scored), 4) if scored else 0.0,
        total_cer_errors=total_cer_errors,
        total_cer_reference=total_cer_reference,
        total_wer_errors=total_wer_errors,
        total_wer_reference=total_wer_reference,
        per_category=categories,
    )
