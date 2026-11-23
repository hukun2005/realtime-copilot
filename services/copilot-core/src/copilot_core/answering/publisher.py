"""P52-03：有界答案事件发布/订阅。"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from .events import RuntimeEvent

TERMINAL_TYPES = {"answer.completed", "answer.cancelled", "answer.failed"}


@dataclass
class Subscription:
    session_id: str
    id: str = field(default_factory=lambda: "sub_" + uuid.uuid4().hex)
    maxsize: int = 100
    queue: asyncio.Queue[RuntimeEvent | None] = field(init=False)
    dropped_delta: int = 0
    overflowed: bool = False

    def __post_init__(self) -> None:
        self.queue = asyncio.Queue(maxsize=self.maxsize)

    async def get(self) -> RuntimeEvent | None:
        return await self.queue.get()

    def close(self) -> None:
        if not self.queue.full():
            self.queue.put_nowait(None)


class AnswerEventPublisher:
    """每个 session 独立订阅；delta 可丢，终态不可静默丢。"""

    def __init__(self) -> None:
        self._subs: dict[str, dict[str, Subscription]] = {}
        self.dropped_delta = 0
        self.overflows = 0

    def subscribe(self, session_id: str, *, maxsize: int = 100) -> Subscription:
        sub = Subscription(session_id=session_id, maxsize=maxsize)
        self._subs.setdefault(session_id, {})[sub.id] = sub
        return sub

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subs.get(subscription.session_id, {}).pop(subscription.id, None)
        subscription.close()

    async def publish(self, event: RuntimeEvent) -> None:
        for sub in list(self._subs.get(event.session_id, {}).values()):
            self._offer(sub, event)

    def _offer(self, sub: Subscription, event: RuntimeEvent) -> None:
        if not sub.queue.full():
            sub.queue.put_nowait(event)
            return
        if event.type not in TERMINAL_TYPES:
            dropped = sub.queue.get_nowait()
            if dropped is not None and dropped.type not in TERMINAL_TYPES:
                sub.dropped_delta += 1
                self.dropped_delta += 1
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                sub.overflowed = True
                self.overflows += 1
            return
        # 终态优先：只淘汰一个 delta，保留其余事件
        items: list[RuntimeEvent | None] = []
        while not sub.queue.empty():
            items.append(sub.queue.get_nowait())
        non_terminal = next(
            (index for index, item in enumerate(items) if item is not None and item.type not in TERMINAL_TYPES),
            None,
        )
        if non_terminal is None:
            sub.overflowed = True
            self.overflows += 1
            return
        items.pop(non_terminal)
        sub.dropped_delta += 1
        self.dropped_delta += 1
        for item in items:
            if item is not None:
                sub.queue.put_nowait(item)
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            sub.overflowed = True
            self.overflows += 1
