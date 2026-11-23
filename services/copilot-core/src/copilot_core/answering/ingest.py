"""P51-02：ASR Final 输入校验与有限去重。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

CJK = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")
PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)
MAX_TEXT = 4000
MIN_CHARS = 2


def is_meaningful(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > MAX_TEXT:
        return False
    if not (CJK.search(stripped) or LATIN.search(stripped)):
        return False
    return len(PUNCT.sub("", stripped)) >= MIN_CHARS


@dataclass
class _Seen:
    at: float
    text: str


class FinalGuard:
    """按 event/segment/source 去重，不按文本永久去重。"""

    def __init__(self, *, ttl_seconds: float = 600.0, max_entries: int = 2000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._events: dict[str, _Seen] = {}
        self._segments: dict[tuple[str, str, str], _Seen] = {}

    def accept(
        self,
        *,
        event_id: str,
        session_id: str,
        source: str,
        segment_id: str,
        text: str,
        now: float | None = None,
    ) -> bool:
        if source not in ("loopback", "mic") or not segment_id or not is_meaningful(text):
            return False
        timestamp = now if now is not None else time.monotonic()
        self._purge(timestamp)
        event_key = event_id or f"{session_id}:{source}:{segment_id}"
        if event_key in self._events:
            return False
        segment_key = (session_id, source, segment_id)
        if segment_key in self._segments:
            return False
        self._events[event_key] = _Seen(timestamp, text)
        self._segments[segment_key] = _Seen(timestamp, text)
        return True

    def _purge(self, now: float) -> None:
        expired = [
            key for key, item in self._events.items() if now - item.at > self.ttl_seconds
        ]
        for key in expired:
            self._events.pop(key, None)
        expired_segments = [
            key for key, item in self._segments.items() if now - item.at > self.ttl_seconds
        ]
        for key in expired_segments:
            self._segments.pop(key, None)
        while len(self._events) > self.max_entries:
            self._events.pop(next(iter(self._events)))
        while len(self._segments) > self.max_entries:
            self._segments.pop(next(iter(self._segments)))
