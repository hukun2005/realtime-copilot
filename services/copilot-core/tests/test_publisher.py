"""P52-03：session 订阅、有界队列与终态优先。"""

from __future__ import annotations

from copilot_core.answering import AnswerEventPublisher
from copilot_core.answering.events import RuntimeEvent


def event(kind: str, session: str = "s1", seq: int = 1) -> RuntimeEvent:
    return RuntimeEvent(
        type=kind,
        session_id=session,
        request_id="req_1",
        question_id="q_1",
        generation=1,
        sequence=seq,
    )


def drain(sub) -> list[RuntimeEvent]:
    items = []
    while not sub.queue.empty():
        item = sub.queue.get_nowait()
        if item is None:
            break
        items.append(item)
    return items


async def test_sessions_are_isolated() -> None:
    publisher = AnswerEventPublisher()
    one = publisher.subscribe("s1")
    two = publisher.subscribe("s2")
    await publisher.publish(event("answer.delta", "s1"))
    assert len(drain(one)) == 1
    assert drain(two) == []


async def test_bounded_queue_keeps_terminal() -> None:
    publisher = AnswerEventPublisher()
    sub = publisher.subscribe("s1", maxsize=2)
    for seq in range(1, 6):
        await publisher.publish(event("answer.delta", seq=seq))
    await publisher.publish(event("answer.completed", seq=6))
    items = drain(sub)
    assert [item.type for item in items] == ["answer.delta", "answer.completed"]
    assert publisher.dropped_delta >= 3


async def test_unsubscribe_stops_delivery() -> None:
    publisher = AnswerEventPublisher()
    sub = publisher.subscribe("s1")
    publisher.unsubscribe(sub)
    await publisher.publish(event("answer.delta"))
    assert drain(sub) == []


async def test_publish_does_not_wait_for_slow_subscriber() -> None:
    publisher = AnswerEventPublisher()
    publisher.subscribe("s1", maxsize=1)
    for seq in range(1, 101):
        await publisher.publish(event("answer.delta", seq=seq))
    assert True  # publish 全部返回即证明没有阻塞

