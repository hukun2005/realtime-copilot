"""P34-02：TranscriptBuffer 有界滚动、快照、gap 与 generation。"""

from __future__ import annotations

from copilot_core.transcript import TranscriptBuffer, TranscriptSegment

MS = 1_000_000


def segment(
    segment_id: str,
    start_ms: int,
    text: str,
    *,
    source: str = "loopback",
    duration_ms: int = 100,
    generation: int = 1,
) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=segment_id,
        source=source,
        start_ts_ns=start_ms * MS,
        end_ts_ns=(start_ms + duration_ms) * MS,
        text=text,
        revision=1,
        generation=generation,
    )


def test_snapshot_orders_by_start() -> None:
    buffer = TranscriptBuffer(generation=1)
    buffer.append(segment("seg_b", 3_000, "第二句"))
    buffer.append(segment("seg_a", 0, "第一句"))

    snapshot = buffer.snapshot()
    assert [item.segment_id for item in snapshot] == ["seg_a", "seg_b"]


def test_sources_are_independent() -> None:
    buffer = TranscriptBuffer(generation=1)
    buffer.append(segment("loop", 3_000, "远端", source="loopback"))
    buffer.append(segment("mic", 0, "本地", source="mic"))

    assert [item.segment_id for item in buffer.snapshot(source="mic")] == ["mic"]
    merged = buffer.snapshot()
    assert [item.segment_id for item in merged] == ["mic", "loop"]


def test_duplicate_segment_is_ignored() -> None:
    buffer = TranscriptBuffer(generation=1)
    buffer.append(segment("seg_a", 0, "你好"))
    buffer.append(segment("seg_a", 0, "你好"))

    assert buffer.stats.duplicates == 1
    assert len(buffer.snapshot()) == 1


def test_stale_generation_is_dropped() -> None:
    buffer = TranscriptBuffer(generation=2)
    buffer.append(segment("seg_old", 0, "旧", generation=1))

    assert buffer.snapshot() == []
    assert buffer.stats.stale == 1


def test_max_segments_evicts_oldest() -> None:
    buffer = TranscriptBuffer(generation=1, max_segments_per_source=2)
    for index in range(3):
        buffer.append(segment(f"seg_{index}", index * 5_000, f"句子{index}"))

    snapshot = buffer.snapshot()
    assert [item.segment_id for item in snapshot] == ["seg_1", "seg_2"]
    assert buffer.stats.evicted_segments == 1
    assert buffer.stats.segment_count == 2


def test_max_chars_evicts() -> None:
    buffer = TranscriptBuffer(generation=1, max_chars=5)
    buffer.append(segment("seg_a", 0, "一二三"))
    buffer.append(segment("seg_b", 5_000, "四五六"))

    assert buffer.stats.evicted_segments == 1
    assert buffer.stats.char_count <= 6


def test_gap_is_recorded() -> None:
    buffer = TranscriptBuffer(generation=1, gap_threshold_ms=2_000)
    buffer.append(segment("seg_a", 0, "第一句"))
    buffer.append(segment("seg_b", 10_000, "很久以后"))

    assert buffer.stats.gaps == 1
    gaps = buffer.gaps()
    assert gaps[0].start_ts_ns == 100 * MS
    assert gaps[0].end_ts_ns == 10_000 * MS


def test_out_of_order_is_sorted_and_counted() -> None:
    buffer = TranscriptBuffer(generation=1)
    buffer.append(segment("seg_b", 3_000, "第二句"))
    buffer.append(segment("seg_a", 0, "第一句"))

    assert buffer.stats.out_of_order == 1
    assert [item.segment_id for item in buffer.snapshot()] == ["seg_a", "seg_b"]


def test_recent_text_keeps_tail() -> None:
    buffer = TranscriptBuffer(generation=1)
    buffer.append(segment("seg_a", 0, "第一句"))
    buffer.append(segment("seg_b", 5_000, "第二句"))

    text = buffer.recent_text(max_chars=4)
    assert text == "第二句"


def test_clear_and_reset() -> None:
    buffer = TranscriptBuffer(generation=1)
    buffer.append(segment("seg_a", 0, "旧"))

    buffer.reset(2)
    buffer.append(segment("seg_a", 0, "新", generation=2))

    assert buffer.generation == 2
    assert [item.text for item in buffer.snapshot()] == ["新"]
    assert buffer.stats.duplicates == 0


def test_empty_text_is_ignored() -> None:
    buffer = TranscriptBuffer(generation=1)
    buffer.append(segment("seg_empty", 0, "   "))

    assert buffer.snapshot() == []
    assert buffer.stats.appended == 0


def test_snapshot_is_a_copy() -> None:
    buffer = TranscriptBuffer(generation=1)
    buffer.append(segment("seg_a", 0, "第一句"))
    snapshot = buffer.snapshot()
    buffer.append(segment("seg_b", 5_000, "第二句"))

    assert len(snapshot) == 1
