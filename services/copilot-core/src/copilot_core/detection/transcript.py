"""P4.1：转写文本管理（partial/final、去重、generation 隔离、双来源）。"""

from __future__ import annotations

import time
from dataclasses import dataclass

MAX_TEXT_CHARS = 4000
SOURCES = ("loopback", "mic")


@dataclass
class TranscriptEntry:
    segment_id: str
    source: str
    text: str
    generation: int
    is_final: bool = False
    updated_at: float = 0.0
    revision: int = 0

    @property
    def normalized(self) -> str:
        return normalize_text(self.text)


class TranscriptLog:
    """内存转写账本；不修改 ASR 原始文本。"""

    def __init__(self, *, generation: int = 0, max_entries: int = 500) -> None:
        self.generation = generation
        self.max_entries = max_entries
        self._entries: dict[tuple[str, str], TranscriptEntry] = {}
        self._finals: set[tuple[str, str]] = set()
        self._order: list[tuple[str, str]] = []

    def ingest_partial(
        self,
        *,
        segment_id: str,
        source: str,
        text: str,
        generation: int | None = None,
    ) -> TranscriptEntry | None:
        return self._ingest(
            segment_id=segment_id,
            source=source,
            text=text,
            generation=generation,
            final=False,
        )

    def ingest_final(
        self,
        *,
        segment_id: str,
        source: str,
        text: str,
        generation: int | None = None,
    ) -> TranscriptEntry | None:
        return self._ingest(
            segment_id=segment_id,
            source=source,
            text=text,
            generation=generation,
            final=True,
        )

    def _ingest(
        self,
        *,
        segment_id: str,
        source: str,
        text: str,
        generation: int | None,
        final: bool,
    ) -> TranscriptEntry | None:
        if generation is not None and generation != self.generation:
            return None
        if source not in SOURCES or not segment_id or not text.strip():
            return None
        key = (source, segment_id)
        clipped = text[:MAX_TEXT_CHARS]
        current = self._entries.get(key)
        if current is not None and current.is_final:
            return None
        if final and key in self._finals:
            return None

        entry = TranscriptEntry(
            segment_id=segment_id,
            source=source,
            text=clipped,
            generation=self.generation,
            is_final=final,
            updated_at=time.monotonic(),
            revision=(current.revision + 1) if current else 1,
        )
        if current is None:
            self._order.append(key)
        self._entries[key] = entry
        if final:
            self._finals.add(key)
        self._trim()
        return entry

    def recent(self, limit: int = 10, *, source: str | None = None) -> list[TranscriptEntry]:
        entries = [
            self._entries[key]
            for key in self._order
            if key in self._entries and (source is None or key[0] == source)
        ]
        entries.sort(key=lambda item: item.updated_at)
        return entries[-limit:]

    def final_entries(self, *, source: str | None = None) -> list[TranscriptEntry]:
        entries = self.recent(len(self._order), source=source)
        return [entry for entry in entries if entry.is_final]

    def reset(self, generation: int) -> None:
        self.generation = generation
        self._entries.clear()
        self._finals.clear()
        self._order.clear()

    def _trim(self) -> None:
        while len(self._order) > self.max_entries:
            oldest = self._order.pop(0)
            self._entries.pop(oldest, None)
            self._finals.discard(oldest)


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())
