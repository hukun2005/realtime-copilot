"""P54-04：取消答案 API。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_cancel_answer(client: TestClient, token: str) -> None:
    calls: list[str] = []

    class Runtime:
        async def cancel(self, request_id: str, reason: str = "user_cancelled") -> bool:
            calls.append(request_id)
            return True

    client.app.state.answer_runtime = Runtime()
    response = client.post(
        "/api/answers/cancel",
        headers={"X-Copilot-Token": token},
        json={"requestId": "req_1"},
    )
    assert response.status_code == 200
    assert response.json() == {"cancelled": True}
    assert calls == ["req_1"]


def test_cancel_requires_runtime(client: TestClient, token: str) -> None:
    client.app.state.answer_runtime = None
    response = client.post(
        "/api/answers/cancel",
        headers={"X-Copilot-Token": token},
        json={"requestId": "req_1"},
    )
    assert response.status_code == 503


async def test_capture_stop_cancels_session(client: TestClient) -> None:
    calls: list[str] = []

    class Handler:
        session_id = "sess_1"

        async def stop(self, *, reason: str) -> None:
            calls.append(reason)

    class Runtime:
        async def cancel_session(self, session_id: str) -> int:
            calls.append(session_id)
            return 1

    client.app.state.transcript_handler = Handler()
    client.app.state.answer_runtime = Runtime()
    await client.app.state.transcript_stop()
    assert "sess_1" in calls
    assert "capture_stopped" in calls
