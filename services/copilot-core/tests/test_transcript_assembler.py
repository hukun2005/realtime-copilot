"""P34-01：TranscriptAssembler 排序、去重、合并与 generation 隔离。"""

from __future__ import annotations

from copilot_core.transcript import TranscriptAssembler

MS = 1_000_000


def accept(
    assembler: TranscriptAssembler,
    segment_id: str,
    start_ms: int,
    text: str,
    *,
    source: str = "loopback",
    duration_ms: int = 100,
    generation: int | None = None,
) -> list:
    return assembler.accept(
        segment_id=segment_id,
        source=source,
        start_ts_ns=start_ms * MS,
        end_ts_ns=(start_ms + duration_ms) * MS,
        text=text,
        generation=generation,
    )


def test_flush_orders_segments_by_start() -> None:
    assembler = TranscriptAssembler(generation=1)

    accept(assembler, "seg_a", 0, "第一句")
    accept(assembler, "seg_b", 3_000, "第二句")

    emitted = assembler.flush()
    assert [item.segment_id for item in emitted] == ["seg_a", "seg_b"]
    assert [item.text for item in emitted] == ["第一句", "第二句"]
    assert assembler.stats.emitted == 2


def test_duplicate_segment_is_ignored() -> None:
    assembler = TranscriptAssembler(generation=1)
    accept(assembler, "seg_a", 0, "你好")

    emitted = accept(assembler, "seg_a", 0, "你好")

    assert emitted == []
    assert assembler.stats.duplicates == 1
    assert assembler.stats.accepted == 1


def test_out_of_order_is_reordered() -> None:
    assembler = TranscriptAssembler(generation=1)
    accept(assembler, "seg_b", 3_000, "第二句")
    accept(assembler, "seg_a", 0, "第一句")

    emitted = assembler.flush()

    assert [item.segment_id for item in emitted] == ["seg_a", "seg_b"]
    assert assembler.stats.out_of_order == 1


def test_adjacent_segments_merge() -> None:
    assembler = TranscriptAssembler(generation=1, merge_gap_ms=200)

    accept(assembler, "seg_a", 0, "你好", duration_ms=100)
    accept(assembler, "seg_b", 150, "世界", duration_ms=100)

    emitted = assembler.flush()
    assert len(emitted) == 1
    assert emitted[0].text == "你好世界"
    assert emitted[0].start_ts_ns == 0
    assert emitted[0].end_ts_ns == 250 * MS
    assert assembler.stats.merged == 1


def test_english_merge_keeps_space() -> None:
    assembler = TranscriptAssembler(generation=1, merge_gap_ms=200)
    accept(assembler, "seg_a", 0, "hello", duration_ms=100)
    accept(assembler, "seg_b", 150, "world", duration_ms=100)

    emitted = assembler.flush()
    assert emitted[0].text == "hello world"


def test_empty_text_is_ignored() -> None:
    assembler = TranscriptAssembler(generation=1)

    accept(assembler, "seg_empty", 0, "   ")
    accept(assembler, "seg_punct", 100, "，。")

    assert assembler.flush() == []
    assert assembler.stats.empty == 2


def test_generation_mismatch_is_stale() -> None:
    assembler = TranscriptAssembler(generation=1)
    accept(assembler, "seg_old", 0, "旧会话", generation=1)

    emitted = accept(assembler, "seg_stale", 100, "迟到", generation=2)

    assert emitted == []
    assert assembler.stats.stale == 1
    assert [item.segment_id for item in assembler.flush()] == ["seg_old"]


def test_reset_clears_buffer_and_seen() -> None:
    assembler = TranscriptAssembler(generation=1)
    accept(assembler, "seg_a", 0, "旧")
    assembler.reset(2)

    accept(assembler, "seg_a", 0, "新", generation=2)
    emitted = assembler.flush()

    assert assembler.generation == 2
    assert assembler.stats.duplicates == 0
    assert [item.text for item in emitted] == ["新"]


def test_pending_is_bounded_and_oldest_emitted_first() -> None:
    assembler = TranscriptAssembler(generation=1, reorder_window=2, max_pending=4)

    for index in range(5):
        accept(assembler, f"seg_{index}", index * 5_000, f"句子{index}")

    assert assembler.stats.overflow_emitted >= 1
    pending = assembler._pending["loopback"]
    assert len(pending) <= 4
    assert assembler.flush()[-1].text == "句子4"


def test_sources_are_independent() -> None:
    assembler = TranscriptAssembler(generation=1)

    accept(assembler, "loop_1", 3_000, "远端", source="loopback")
    accept(assembler, "mic_1", 0, "本地", source="mic")

    emitted = assembler.flush()
    by_source = {item.source: item.text for item in emitted}
    assert by_source == {"loopback": "远端", "mic": "本地"}


def test_large_gap_is_counted() -> None:
    assembler = TranscriptAssembler(generation=1, gap_threshold_ms=2_000)

    accept(assembler, "seg_a", 0, "第一句")
    accept(assembler, "seg_b", 10_000, "很久以后")
    assembler.flush()

    assert assembler.stats.gaps == 1


def test_revision_and_provider_are_preserved() -> None:
    assembler = TranscriptAssembler(generation=3)
    assembler.accept(
        segment_id="seg_x",
        source="mic",
        start_ts_ns=0,
        end_ts_ns=MS,
        text="文本",
        revision=4,
        generation=3,
        provider="paraformer",
    )

    emitted = assembler.flush()[0]
    assert emitted.revision == 4
    assert emitted.provider == "paraformer"
    assert emitted.generation == 3
