"""P35-04：Segment 延迟分位与汇总。"""

from __future__ import annotations

from copilot_core.evaluation import LatencyRecord, percentile, summarize


def test_percentile_empty_and_single() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([123.4], 50) == 123.4
    assert percentile([123.4], 95) == 123.4


def test_percentile_nearest_rank() -> None:
    values = [10, 20, 30, 40]
    assert percentile(values, 0) == 10
    assert percentile(values, 50) == 20
    assert percentile(values, 95) == 40
    assert percentile(values, 100) == 40


def test_summarize_excludes_missing_and_errors() -> None:
    records = [
        LatencyRecord("a", "clean", 3.0, 120, 400, 1, finalization_ms=180),
        LatencyRecord("b", "clean", 3.0, 150, 500, 1, finalization_ms=250),
        LatencyRecord("s", "silence", 3.0, None, None, 0),
        LatencyRecord("e", "clean", 3.0, 100, 900, 1, error="boom"),
    ]
    result = summarize(records)
    assert result["samples"] == 4
    assert result["withFinal"] == 2
    assert result["withPartial"] == 2
    assert result["errors"] == 1
    assert result["noFinal"] == ["s"]
    assert result["finalMinMs"] == 400
    assert result["finalMaxMs"] == 500
    assert result["finalP50Ms"] == 400
    assert result["finalizationP50Ms"] == 180
    assert result["finalizationP95Ms"] == 250


def test_summarize_all_missing() -> None:
    result = summarize([LatencyRecord("s", "silence", 3.0, None, None, 0)])
    assert result["withFinal"] == 0
    assert result["finalP50Ms"] == 0.0
    assert result["finalMinMs"] is None
