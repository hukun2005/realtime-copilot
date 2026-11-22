"""转写滚动缓冲：每来源有界保存最近转写，供上下文与快照使用。

设计约束：

- 以 Segment 条数与字符数双上限控制内存；超限时淘汰最早（按 startTs）的 Segment。
- 每个来源独立排序与保留，`snapshot()` 可按来源或时间线读取。
- 重复 `segment_id` 幂等忽略；旧 generation 丢弃。
- 记录 gap（时间断层）与乱序计数，不插入占位文本。
- 不依赖 FastAPI / WebSocket / SQLite。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from .assembler import TranscriptSegment

DEFAULT_MAX_SEGMENTS_PER_SOURCE: Final = 500
DEFAULT_MAX_CHARS: Final = 200_000
DEFAULT_GAP_THRESHOLD_MS: Final = 2_000
MAX_GAP_RANGES: Final = 100


@dataclass
class BufferStats:
    """缓冲计数。"""

    appended: int = 0
    duplicates: int = 0
    stale: int = 0
    out_of_order: int = 0
    gaps: int = 0
    evicted_segments: int = 0
    evicted_chars: int = 0
    segment_count: int = 0
    char_count: int = 0


@dataclass(frozen=True)
class GapRange:
    """两个相邻 Segment 之间的时间断层。"""

    source: str
    start_ts_ns: int
    end_ts_ns: int


@dataclass
class TranscriptBuffer:
    """有界滚动转写缓冲。"""

    generation: int = 0
    max_segments_per_source: int = DEFAULT_MAX_SEGMENTS_PER_SOURCE
    max_chars: int = DEFAULT_MAX_CHARS
    gap_threshold_ms: int = DEFAULT_GAP_THRESHOLD_MS
    stats: BufferStats = field(default_factory=BufferStats)
    _segments: dict[str, list[TranscriptSegment]] = field(default_factory=dict, init=False)
    _ids: set[str] = field(default_factory=set, init=False)
    _last_start: dict[str, int] = field(default_factory=dict, init=False)
    _last_end: dict[str, int] = field(default_factory=dict, init=False)
    _gaps: list[GapRange] = field(default_factory=list, init=False)
    _char_count: int = field(default=0, init=False)

    def append(self, segment: TranscriptSegment) -> list[TranscriptSegment]:
        """加入一条装配后的 Segment，返回被淘汰的 Segment。"""
        if segment.generation != self.generation:
            self.stats.stale += 1
            return []
        text = segment.text.strip()
        if not text:
            return []
        if segment.segment_id in self._ids:
            self.stats.duplicates += 1
            return []

        self._ids.add(segment.segment_id)
        self.stats.appended += 1
        items = self._segments.setdefault(segment.source, [])

        last_end = self._last_end.get(segment.source)
        if last_end is not None:
            gap_ns = segment.start_ts_ns - last_end
            if gap_ns > self.gap_threshold_ms * 1_000_000:
                self.stats.gaps += 1
                if len(self._gaps) >= MAX_GAP_RANGES:
                    del self._gaps[0]
                self._gaps.append(
                    GapRange(
                        source=segment.source,
                        start_ts_ns=last_end,
                        end_ts_ns=segment.start_ts_ns,
                    )
                )

        last_start = self._last_start.get(segment.source)
        if last_start is not None and segment.start_ts_ns < last_start:
            self.stats.out_of_order += 1

        insert_at = len(items)
        while insert_at > 0 and items[insert_at - 1].start_ts_ns > segment.start_ts_ns:
            insert_at -= 1
        items.insert(insert_at, segment)
        self._last_start[segment.source] = max(last_start or 0, segment.start_ts_ns)
        self._last_end[segment.source] = max(last_end or 0, segment.end_ts_ns)
        self._char_count += len(text)

        return self._evict()

    def _evict(self) -> list[TranscriptSegment]:
        evicted: list[TranscriptSegment] = []
        while self._over_limit():
            oldest = self._oldest()
            if oldest is None:
                break
            items = self._segments[oldest[0]]
            items.remove(oldest[1])
            self._ids.discard(oldest[1].segment_id)
            self._char_count -= len(oldest[1].text)
            self.stats.evicted_segments += 1
            self.stats.evicted_chars += len(oldest[1].text)
            evicted.append(oldest[1])
        self.stats.segment_count = sum(len(items) for items in self._segments.values())
        self.stats.char_count = self._char_count
        return evicted

    def _over_limit(self) -> bool:
        if self._char_count > self.max_chars:
            return True
        return any(len(items) > self.max_segments_per_source for items in self._segments.values())

    def _oldest(self) -> tuple[str, TranscriptSegment] | None:
        best: tuple[str, TranscriptSegment] | None = None
        for source, items in self._segments.items():
            if not items:
                continue
            candidate = items[0]
            if best is None or candidate.start_ts_ns < best[1].start_ts_ns:
                best = (source, candidate)
        return best

    def snapshot(self, *, source: str | None = None) -> list[TranscriptSegment]:
        """返回只读快照（按时间线排序）。"""
        if source is not None:
            return list(self._segments.get(source, []))
        merged: list[TranscriptSegment] = []
        for items in self._segments.values():
            merged.extend(items)
        merged.sort(key=lambda item: (item.start_ts_ns, item.source))
        return merged

    def recent_text(self, *, max_chars: int = 2_000, source: str | None = None) -> str:
        """返回最近转写文本，最多 ``max_chars`` 个字符（保留尾部）。"""
        segments = self.snapshot(source=source)
        chunks: list[str] = []
        total = 0
        for segment in reversed(segments):
            text = segment.text
            if total + len(text) + 1 > max_chars:
                remaining = max_chars - total
                if remaining > 0:
                    chunks.append(text[-remaining:])
                break
            chunks.append(text)
            total += len(text) + 1
        return "\n".join(reversed(chunks))

    def gaps(self) -> list[GapRange]:
        return list(self._gaps)

    def clear(self) -> None:
        """清空缓冲但保留 generation。"""
        self._segments.clear()
        self._ids.clear()
        self._last_start.clear()
        self._last_end.clear()
        self._gaps.clear()
        self._char_count = 0
        self.stats.segment_count = 0
        self.stats.char_count = 0

    def reset(self, generation: int) -> None:
        """开始新会话：清空并切换 generation。"""
        self.clear()
        self.generation = generation
