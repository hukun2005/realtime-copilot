"""Segment 级延迟统计（不是端到端面试回答延迟）。

每个样本记录：

- 音频时长；
- 首次 partial 相对 ``transcribe`` 开始的毫秒数；
- final 相对 ``transcribe`` 开始的毫秒数；
- final 数量。

无语音样本可能没有 final，不进入延迟分位；单独报告 excluded。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class LatencyRecord:
    sample_id: str
    category: str
    audio_seconds: float
    first_partial_ms: float | None
    final_ms: float | None
    finals: int
    finalization_ms: float | None = None
    error: str | None = None


def percentile(values: Iterable[float], percent: float) -> float:
    """最近秩百分位；空序列返回 0。"""
    items = sorted(values)
    if not items:
        return 0.0
    if percent <= 0:
        return items[0]
    if percent >= 100:
        return items[-1]
    rank = max(0, math.ceil(percent / 100 * len(items)) - 1)
    return items[min(rank, len(items) - 1)]


def summarize(records: Iterable[LatencyRecord]) -> dict[str, object]:
    items = list(records)
    finals = [item.final_ms for item in items if item.final_ms is not None and item.error is None]
    partials = [
        item.first_partial_ms
        for item in items
        if item.first_partial_ms is not None and item.error is None
    ]
    finalizations = [
        item.finalization_ms
        for item in items
        if item.finalization_ms is not None and item.error is None
    ]
    errors = [item for item in items if item.error is not None]
    no_final = [item.sample_id for item in items if item.final_ms is None and item.error is None]
    return {
        "samples": len(items),
        "withFinal": len(finals),
        "withPartial": len(partials),
        "errors": len(errors),
        "noFinal": no_final,
        "finalP50Ms": round(percentile(finals, 50), 1),
        "finalP95Ms": round(percentile(finals, 95), 1),
        "finalMinMs": round(min(finals), 1) if finals else None,
        "finalMaxMs": round(max(finals), 1) if finals else None,
        "finalizationP50Ms": round(percentile(finalizations, 50), 1),
        "finalizationP95Ms": round(percentile(finalizations, 95), 1),
        "finalizationMinMs": round(min(finalizations), 1) if finalizations else None,
        "finalizationMaxMs": round(max(finalizations), 1) if finalizations else None,
        "firstPartialP50Ms": round(percentile(partials, 50), 1),
        "firstPartialP95Ms": round(percentile(partials, 95), 1),
        "firstPartialMinMs": round(min(partials), 1) if partials else None,
        "firstPartialMaxMs": round(max(partials), 1) if partials else None,
    }
