"""RI-6b：知识库/RAG Core API。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_knowledge_search_and_rag(client: TestClient, token: str) -> None:
    headers = {"X-Copilot-Token": token}
    assert client.get("/api/knowledge/search", params={"q": "Pinia"}, headers=headers).json() == []

    response = client.post(
        "/api/rag/context",
        headers=headers,
        json={"question": "为什么项目使用 Pinia？", "projectId": "rp_1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["detected"] is True
    assert body["projectEvidence"] == []
    assert "project_evidence" in body["missing"]
