"""转写装配：把 ASR final 按来源排序、去重并合并为稳定的转写 Segment。

本模块不依赖 FastAPI、WebSocket 或 SQLite；它只接收已定稿的 ASR 文本，
输出可直接持久化/广播的 :class:`TranscriptSegment`。

排序与延迟：

- 每个来源有一个待装配窗口（``reorder_window``）。窗口内按 ``start_ts_ns`` 排序；
  当窗口超出容量时先产出最早的一条，保证缓冲有界。
- 相邻 Segment 的间隙不超过 ``merge_gap_ms`` 时合并为一条，避免 VAD 把一句话切断。
- 间隙超过 ``gap_threshold_ms`` 时只计数（``gaps``），不插入占位文本。

去重以 ``(generation, segment_id)`` 为键；不同 generation 的迟到结果直接丢弃。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

DEFAULT_REORDER_WINDOW: Final = 3
DEFAULT_MERGE_GAP_MS: Final = 200
DEFAULT_GAP_THRESHOLD_MS: Final = 2_000
DEFAULT_MAX_PENDING: Final = 32


@dataclass(frozen=True)
class TranscriptSegment:
    """装配后的稳定转写片段。"""

    segment_id: str
    source: str
    start_ts_ns: int
    end_ts_ns: int
    text: str
    revision: int
    generation: int
    provider: str | None = None


@dataclass
class AssemblerStats:
    """装配计数，用于 healthz/诊断。"""

    accepted: int = 0
    emitted: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    merged: int = 0
    gaps: int = 0
    stale: int = 0
    empty: int = 0
    overflow_emitted: int = 0


class TranscriptAssembler:
    """按来源装配 ASR final。"""

    def __init__(
        self,
        *,
        generation: int = 0,
        reorder_window: int = DEFAULT_REORDER_WINDOW,
        merge_gap_ms: int = DEFAULT_MERGE_GAP_MS,
        gap_threshold_ms: int = DEFAULT_GAP_THRESHOLD_MS,
        max_pending: int = DEFAULT_MAX_PENDING,
    ) -> None:
        if reorder_window < 1:
            raise ValueError("reorder_window must be >= 1")
        if max_pending < reorder_window:
            raise ValueError("max_pending must be >= reorder_window")
        self._generation = generation
        self._window = reorder_window
        self._merge_gap_ms = merge_gap_ms
        self._gap_threshold_ms = gap_threshold_ms
        self._max_pending = max_pending
        self._pending: dict[str, list[TranscriptSegment]] = {}
        self._last_emitted_end: dict[str, int] = {}
        self._seen: set[tuple[int, str]] = set()
        self.stats = AssemblerStats()

    @property
    def generation(self) -> int:
        return self._generation

    def reset(self, generation: int) -> None:
        """开始新会话：丢弃未产出的缓冲与去重记录。"""
        self._generation = generation
        self._pending.clear()
        self._last_emitted_end.clear()
        self._seen.clear()

    def accept(
        self,
        *,
        segment_id: str,
        source: str,
        start_ts_ns: int,
        end_ts_ns: int,
        text: str,
        revision: int = 1,
        generation: int | None = None,
        provider: str | None = None,
    ) -> list[TranscriptSegment]:
        """接收一条 ASR final，返回本次可产出的稳定 Segment。"""
        if generation is not None and generation != self._generation:
            self.stats.stale += 1
            return []

        normalized = " ".join(text.split())
        if not normalized or not any(char.isalnum() for char in normalized):
            self.stats.empty += 1
            return []

        key = (self._generation, segment_id)
        if key in self._seen:
            self.stats.duplicates += 1
            return []
        self._seen.add(key)
        self.stats.accepted += 1

        incoming = TranscriptSegment(
            segment_id=segment_id,
            source=source,
            start_ts_ns=start_ts_ns,
            end_ts_ns=max(end_ts_ns, start_ts_ns),
            text=normalized,
            revision=revision,
            generation=self._generation,
            provider=provider,
        )

        pending = self._pending.setdefault(source, [])
        insert_at = len(pending)
        while insert_at > 0 and pending[insert_at - 1].start_ts_ns > start_ts_ns:
            insert_at -= 1
        if insert_at < len(pending):
            self.stats.out_of_order += 1
        pending.insert(insert_at, incoming)

        self._merge_pending(source)
        emitted: list[TranscriptSegment] = []
        while len(pending) > self._window:
            emitted.append(pending.pop(0))
            self.stats.overflow_emitted += 1
        self._record_emitted(source, emitted)
        return emitted

    def flush(self) -> list[TranscriptSegment]:
        """产出所有来源剩余缓冲（用于停止/会话结束）。"""
        emitted: list[TranscriptSegment] = []
        for source in list(self._pending):
            self._merge_pending(source)
            items = list(self._pending[source])
            self._pending[source].clear()
            self._record_emitted(source, items)
            emitted.extend(items)
        return emitted

    def _merge_pending(self, source: str) -> None:
        pending = self._pending.get(source)
        if not pending or len(pending) < 2:
            return
        merged: list[TranscriptSegment] = [pending[0]]
        for segment in pending[1:]:
            if self._mergeable(merged[-1], segment):
                merged[-1] = self._merge(merged[-1], segment)
                self.stats.merged += 1
            else:
                merged.append(segment)
        pending[:] = merged

    def _record_emitted(self, source: str, segments: list[TranscriptSegment]) -> None:
        for segment in segments:
            previous_end = self._last_emitted_end.get(source)
            if previous_end is not None and segment.start_ts_ns - previous_end > (
                self._gap_threshold_ms * 1_000_000
            ):
                self.stats.gaps += 1
            self._last_emitted_end[source] = segment.end_ts_ns
            self.stats.emitted += 1

    def _mergeable(self, previous: TranscriptSegment, current: TranscriptSegment) -> bool:
        if current.start_ts_ns < previous.end_ts_ns:
            # 重叠：视为同一句话的重复/切断，合并文本。
            return True
        return current.start_ts_ns - previous.end_ts_ns <= self._merge_gap_ms * 1_000_000

    def _merge(self, previous: TranscriptSegment, current: TranscriptSegment) -> TranscriptSegment:
        return TranscriptSegment(
            segment_id=previous.segment_id,
            source=previous.source,
            start_ts_ns=previous.start_ts_ns,
            end_ts_ns=max(previous.end_ts_ns, current.end_ts_ns),
            text=_join_text(previous.text, current.text),
            revision=previous.revision + current.revision,
            generation=previous.generation,
            provider=previous.provider or current.provider,
        )


def _join_text(left: str, right: str) -> str:
    """中文直接拼接，英文/数字之间保留一个空格。"""
    if not left:
        return right
    if not right:
        return left
    if left[-1].isascii() and left[-1].isalnum() and right[0].isascii() and right[0].isalnum():
        return f"{left} {right}"
    return f"{left}{right}"
