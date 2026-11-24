"""P54-05：DeepSeek 状态 API。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_status_not_configured(client: TestClient, token: str) -> None:
    client.app.state.answer_stack = None
    response = client.get("/api/answer/status", headers={"X-Copilot-Token": token})
    assert response.status_code == 200
    assert response.json()["state"] == "NOT_CONFIGURED"


def test_status_reports_payment_required(client: TestClient, token: str) -> None:
    class Error:
        code = "payment_required"
        status = 402
        retryable = False

    class Client:
        model = "deepseek-flash"
        last_error = Error()

    class Stack:
        client = Client()

        async def close(self) -> None:
            return None

    client.app.state.answer_stack = Stack()
    response = client.get("/api/answer/status", headers={"X-Copilot-Token": token})
    body = response.json()
    assert body["configured"] is True
    assert body["state"] == "PAYMENT_REQUIRED"
    assert body["lastError"]["status"] == 402
