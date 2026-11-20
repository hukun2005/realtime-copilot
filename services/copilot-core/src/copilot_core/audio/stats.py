"""音频接收统计。

只保留计数与时间戳，不保留 PCM 内容。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class AudioCounters:
    """音频通道的累计计数。"""

    frames: int = 0
    bytes_received: int = 0
    gaps: int = 0
    gap_events: int = 0
    duplicates: int = 0
    invalid: int = 0
    too_large: int = 0
    rate_limited: int = 0
    timestamp_regressions: int = 0
    dropped: int = 0
    last_frame_ts_ns: int | None = None
    last_gap_sequence: int | None = None
    error_codes: dict[str, int] = field(default_factory=dict)

    def count_error(self, code: str) -> None:
        """记录一次帧级错误。"""
        self.invalid += 1
        self.error_codes[code] = self.error_codes.get(code, 0) + 1

    def to_json(self) -> dict[str, Any]:
        """转换为 ``/healthz`` 中的 JSON 片段。"""
        payload = asdict(self)
        payload["sessions"] = 0
        return payload
