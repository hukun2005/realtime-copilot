"""P54-06：Mock E2E（ASR final → QuestionEngine → Runtime → Publisher → WS）。"""

from __future__ import annotations

import asyncio
import json

import websockets
from copilot_core.answering import RuntimeEvent
from support import hello_event, recv_until

from copilot_protocol import make_event


async def recv_type(socket, expected: str, *, limit: int = 20) -> dict:
    seen = []
    for _ in range(limit):
        raw = await asyncio.wait_for(socket.recv(), timeout=2)
        message = json.loads(raw)
        seen.append(message.get("type"))
        if message.get("type") == expected:
            return message
    raise AssertionError(f"did not receive {expected}; seen={seen}")


SESSION = "sess_standalone"


def subscribe_event() -> dict:
    return {
        "v": 1,
        "type": "answer.subscribe",
        "eventId": "evt_e2e_subscribe_0001",
        "requestId": "req_01JAY0000000000000000001",
        "ts": 1763700004000,
        "payload": {"sessionId": SESSION},
    }


def final_event(text: str):
    return make_event(
        "asr.final",
        {
            "segmentId": "seg_e2e_1",
            "source": "loopback",
            "startTsNs": 0,
            "endTsNs": 1,
            "revision": 1,
            "text": text,
        },
    )


class FakeRuntime:
    def __init__(self, publish) -> None:
        self.publish = publish
        self.questions = []

    async def submit_question(self, question, *, session_id: str, project_id=None) -> str:
        self.questions.append(question.text)
        await self.publish(RuntimeEvent(
            type="answer.started",
            session_id=session_id,
            request_id="req_e2e_0001",
            question_id=question.id,
            generation=1,
            sequence=1,
            payload={"question": question.text, "questionType": question.question_type.value, "status": "running"},
        ))
        await self.publish(RuntimeEvent(
            type="answer.delta",
            session_id=session_id,
            request_id="req_e2e_0001",
            question_id=question.id,
            generation=1,
            sequence=2,
            payload={"delta": "Mock 答案"},
        ))
        await self.publish(RuntimeEvent(
            type="answer.completed",
            session_id=session_id,
            request_id="req_e2e_0001",
            question_id=question.id,
            generation=1,
            sequence=3,
            payload={"answer": "Mock 答案", "finishReason": "stop", "usage": None},
        ))
        return "req_e2e_0001"

    async def cancel_session(self, session_id: str) -> int:
        return 0


async def test_asr_final_reaches_websocket_answer_events(live_server) -> None:  # type: ignore[no-untyped-def]
    fake = FakeRuntime(live_server.app.state.answer_publisher.publish)
    live_server.app.state.answer_runtime = fake
    async with websockets.connect(live_server.url) as socket:
        await socket.send(hello_event(live_server.token))
        await recv_until(socket, "system.ready")
        await socket.send(json.dumps(subscribe_event()))
        await asyncio.sleep(0.05)

        await live_server.app.state.asr_events(final_event("什么是 Vue3？"))
        assert fake.questions, "runtime was not called"

        started = await recv_type(socket, "answer.started")
        assert started["sessionId"] == SESSION
        assert started["payload"]["question"].startswith("什么是")
        delta = await recv_type(socket, "answer.delta")
        assert delta["payload"]["delta"] == "Mock 答案"
        completed = await recv_type(socket, "answer.completed")
        assert completed["sequence"] == 3
        assert fake.questions and "Vue3" in fake.questions[0]

