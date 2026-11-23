"""RI-6b：GitHub 仓库元数据 API。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_github_repo(monkeypatch, client: TestClient, token: str) -> None:
    class FakeClient:
        def __init__(self, **_kwargs): ...
        async def get_repo(self, owner: str, repo: str):
            from copilot_core.github import Repository
            return Repository(f"{owner}/{repo}", False, "demo", "Python", "u", "main", "2026-11-01T00:00:00Z")
        async def close(self): ...

    monkeypatch.setattr("copilot_core.app.GitHubClient", FakeClient)
    response = client.get("/api/github/repo", params={"full_name": "a/b"}, headers={"X-Copilot-Token": token})
    assert response.status_code == 200
    assert response.json()["full_name"] == "a/b"


def test_github_repo_bad_name(client: TestClient, token: str) -> None:
    response = client.get("/api/github/repo", params={"full_name": "bad"}, headers={"X-Copilot-Token": token})
    assert response.status_code == 400
