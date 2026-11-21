"""ASR 流式片段约束：revision 单调、final 定稿、时间戳单调。

这些规则无法只用 JSON Schema 表达，因此在 Core 侧用一个小状态机强制执行。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum


class AsrViolation(StrEnum):
    """片段更新被拒绝的原因。"""

    STALE_REVISION = "stale_revision"
    SEGMENT_FINALIZED = "segment_finalized"
    INVALID_RANGE = "invalid_range"
    TIMESTAMP_REGRESSION = "timestamp_regression"


class AsrStreamError(ValueError):
    """片段更新违反流式约束。"""

    def __init__(self, violation: AsrViolation, message: str) -> None:
        super().__init__(message)
        self.violation = violation


@dataclass
class _Segment:
    revision: int = -1
    start_ts_ns: int = 0
    end_ts_ns: int = -1
    finalized: bool = False


@dataclass
class AsrStreamTracker:
    """按 segment 维护 revision / 时间戳 / 定稿状态。

    已完成片段按 FIFO 淘汰，避免长期运行时无限增长。
    """

    _segments: dict[str, _Segment] = field(default_factory=dict)
    _finalized: deque[str] = field(default_factory=deque)
    max_segments: int = 512

    def accept_partial(
        self,
        segment_id: str,
        revision: int,
        start_ts_ns: int,
        end_ts_ns: int,
        text: str,
    ) -> None:
        """接收一条 partial；过期 revision、已定稿或时间倒退都拒绝。"""
        self._validate(segment_id, revision, start_ts_ns, end_ts_ns)
        segment = self._segments.setdefault(segment_id, _Segment())
        if segment.finalized:
            raise AsrStreamError(AsrViolation.SEGMENT_FINALIZED, f"{segment_id} is already finalized")
        if revision <= segment.revision:
            raise AsrStreamError(
                AsrViolation.STALE_REVISION,
                f"revision {revision} is not newer than {segment.revision}",
            )
        self._check_monotonic(segment_id, end_ts_ns)
        segment.revision = revision
        segment.start_ts_ns = start_ts_ns
        segment.end_ts_ns = end_ts_ns
        _ = text

    def accept_final(
        self,
        segment_id: str,
        revision: int,
        start_ts_ns: int,
        end_ts_ns: int,
        text: str,
    ) -> None:
        """接收一条 final；必须不早于最近的 revision，且只能定稿一次。"""
        self._validate(segment_id, revision, start_ts_ns, end_ts_ns)
        segment = self._segments.setdefault(segment_id, _Segment())
        if segment.finalized:
            raise AsrStreamError(AsrViolation.SEGMENT_FINALIZED, f"{segment_id} is already finalized")
        if revision < segment.revision:
            raise AsrStreamError(
                AsrViolation.STALE_REVISION,
                f"final revision {revision} is older than {segment.revision}",
            )
        self._check_monotonic(segment_id, end_ts_ns)
        segment.finalized = True
        segment.revision = revision
        segment.start_ts_ns = start_ts_ns
        segment.end_ts_ns = end_ts_ns
        self._finalized.append(segment_id)
        while len(self._finalized) > self.max_segments:
            expired = self._finalized.popleft()
            self._segments.pop(expired, None)
        _ = text

    def is_finalized(self, segment_id: str) -> bool:
        segment = self._segments.get(segment_id)
        return bool(segment and segment.finalized)

    def last_revision(self, segment_id: str) -> int:
        segment = self._segments.get(segment_id)
        return -1 if segment is None else segment.revision

    def _validate(self, segment_id: str, revision: int, start_ts_ns: int, end_ts_ns: int) -> None:
        if revision < 0:
            raise AsrStreamError(AsrViolation.STALE_REVISION, "revision must be >= 0")
        if start_ts_ns > end_ts_ns:
            raise AsrStreamError(
                AsrViolation.INVALID_RANGE,
                f"start {start_ts_ns} is after end {end_ts_ns}",
            )

    def _check_monotonic(self, segment_id: str, end_ts_ns: int) -> None:
        segment = self._segments.get(segment_id)
        if segment is not None and end_ts_ns < segment.end_ts_ns:
            raise AsrStreamError(
                AsrViolation.TIMESTAMP_REGRESSION,
                f"end {end_ts_ns} is before previous {segment.end_ts_ns}",
            )
