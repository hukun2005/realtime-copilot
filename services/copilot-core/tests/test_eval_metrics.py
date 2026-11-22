"""P35-02：CER/WER 指标单元与汇总测试。"""

from __future__ import annotations

from copilot_core.evaluation import (
    aggregate,
    cer_units,
    edit_counts,
    normalize_text,
    score_sample,
    wer_units,
)


def test_normalize_text_removes_punctuation_and_normalizes_width() -> None:
    assert normalize_text("你好，世界！") == "你好世界"
    assert normalize_text("ＡＰＩ 的 P95。") == "api 的 p95"
    assert normalize_text("a\tb\nc") == "a b c"


def test_cer_units_remove_whitespace() -> None:
    assert cer_units("你 好，世 界") == ["你", "好", "世", "界"]


def test_wer_units_split_english_and_cjk() -> None:
    assert wer_units("我们用 Vue3 和 TypeScript") == ["我", "们", "用", "vue3", "和", "typescript"]


def test_perfect_match() -> None:
    score = score_sample(sample_id="a", category="clean", reference="你好世界", hypothesis="你好，世界！")
    assert score.cer == 0.0
    assert score.wer == 0.0
    assert score.cer_counts.errors == 0


def test_substitution() -> None:
    counts = edit_counts(list("你好"), list("你号"))
    assert (counts.substitutions, counts.insertions, counts.deletions) == (1, 0, 0)
    assert counts.rate == 0.5


def test_insertion_and_deletion() -> None:
    assert edit_counts(list("你好"), list("你好啊")).insertions == 1
    assert edit_counts(list("你好啊"), list("你好")).deletions == 1


def test_empty_hypothesis_counts_all_deletions() -> None:
    counts = edit_counts(list("你好"), [])
    assert counts.deletions == 2
    assert counts.rate == 1.0


def test_silence_is_not_hallucination_when_empty() -> None:
    score = score_sample(sample_id="s", category="silence", reference="", hypothesis="")
    assert score.cer == 0.0
    assert score.hallucination is False


def test_silence_hallucination_is_flagged() -> None:
    score = score_sample(sample_id="s", category="silence", reference="", hypothesis="谢谢观看")
    assert score.hallucination is True
    assert score.cer == 1.0


def test_aggregate_micro_and_macro() -> None:
    scores = [
        score_sample(sample_id="a", category="clean", reference="你好", hypothesis="你好"),
        score_sample(sample_id="b", category="clean", reference="世界", hypothesis="世姐"),
        score_sample(sample_id="c", category="technical", reference="Vue3", hypothesis="Vue3"),
        score_sample(sample_id="s", category="silence", reference="", hypothesis=""),
    ]
    result = aggregate(scores)
    assert result.sample_count == 4
    assert result.scored_samples == 3
    assert result.empty_reference_samples == 1
    assert result.total_cer_errors == 1
    assert result.total_cer_reference == 8
    assert result.cer_micro == round(1 / 8, 4)
    assert result.cer_macro == round((0 + 0.5 + 0) / 3, 4)
    assert result.per_category["clean"]["samples"] == 2


def test_aggregate_excludes_silence_from_cer() -> None:
    scores = [
        score_sample(sample_id="a", category="clean", reference="你好", hypothesis="你好"),
        score_sample(sample_id="s", category="silence", reference="", hypothesis="幻觉"),
    ]
    result = aggregate(scores)
    assert result.scored_samples == 1
    assert result.hallucinations == 1
    assert result.cer_micro == 0.0
