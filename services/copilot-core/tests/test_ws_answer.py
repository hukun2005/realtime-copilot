"""P52-04：WS answer.subscribe 真实订阅链路。"""

from __future__ import annotations

import asyncio
import json

import websockets
from copilot_core.answering import RuntimeEvent
from support import hello_event, recv_until

SESSION = "sess_01JAY0000000000000000001"


def subscribe_event() -> dict:
    return {
        "v": 1,
        "type": "answer.subscribe",
        "eventId": "evt_answer_subscribe_0001",
        "requestId": "req_01JAY0000000000000000001",
        "ts": 1763700004000,
        "payload": {"sessionId": SESSION},
    }


def runtime_event(kind: str, sequence: int, payload: dict) -> RuntimeEvent:
    return RuntimeEvent(
        type=kind,
        session_id=SESSION,
        request_id="req_01JAY0000000000000000001",
        question_id="q_01JAY0000000000000000001",
        generation=1,
        sequence=sequence,
        payload=payload,
    )


async def test_subscribe_receives_protocol_events(live_server) -> None:  # type: ignore[no-untyped-def]
    async with websockets.connect(live_server.url) as socket:
        await socket.send(hello_event(live_server.token))
        await recv_until(socket, "system.ready")
        await socket.send(json.dumps(subscribe_event()))
        await asyncio.sleep(0.05)

        publisher = live_server.app.state.answer_publisher
        await publisher.publish(runtime_event("answer.started", 1, {
            "question": "闭包", "questionType": "technical", "status": "running",
        }))
        await publisher.publish(runtime_event("answer.delta", 2, {"delta": "答案"}))
        await publisher.publish(runtime_event("answer.completed", 3, {
            "answer": "答案", "finishReason": "stop", "usage": None,
        }))

        started = await recv_until(socket, "answer.started")
        assert started["sequence"] == 1
        delta = await recv_until(socket, "answer.delta")
        assert delta["payload"]["delta"] == "答案"
        assert delta["sessionId"] == SESSION
        assert delta["sequence"] == 2
        completed = await recv_until(socket, "answer.completed")
        assert completed["payload"]["finishReason"] == "stop"
