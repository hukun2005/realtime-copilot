"""RI-2：GitHub 公开读取、候选匹配与关联持久化。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from copilot_core.github import (
    GitHubClient,
    GitHubError,
    GitHubStore,
    Repository,
    match_candidates,
    parse_github_url,
)
from copilot_core.resume import ResumeProject, ResumeStore
from copilot_core.storage import SCHEMA_VERSION, close, open_database


def repo(name: str = "alice/interview-copilot", language: str = "TypeScript") -> dict:
    return {
        "full_name": name,
        "private": False,
        "description": "Vue3 + FastAPI 面试助手",
        "language": language,
        "html_url": f"https://github.com/{name}",
        "default_branch": "main",
        "pushed_at": "2026-11-01T00:00:00Z",
        "stargazers_count": 3,
    }


def test_parse_url() -> None:
    assert parse_github_url("https://github.com/alice/repo.git") == ("alice", "repo")
    assert parse_github_url("https://gitlab.com/a/b") is None


async def test_public_repo_and_token_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/alice/repo"
        assert request.headers.get("authorization") == "Bearer secret"
        return httpx.Response(200, json=repo("alice/repo"))

    client = GitHubClient(token="secret", transport=httpx.MockTransport(handler))
    try:
        item = await client.get_repo("alice", "repo")
    finally:
        await client.close()
    assert item.full_name == "alice/repo" and item.language == "TypeScript"


@pytest.mark.parametrize(("status", "code"), [(401, "unauthorized"), (403, "rate_limited_or_forbidden"), (404, "not_found"), (429, "rate_limited")])
async def test_http_errors(status: int, code: str) -> None:
    client = GitHubClient(transport=httpx.MockTransport(lambda _request: httpx.Response(status)))
    with pytest.raises(GitHubError) as error:
        await client.get_repo("a", "b")
    assert error.value.code == code
    await client.close()


def test_candidate_scoring_has_reasons() -> None:
    repositories = [
        Repository("alice/interview-copilot", False, "Vue3 面试助手", "TypeScript", "u", "main", "2026-11-20T00:00:00Z"),
        Repository("alice/unrelated", False, "Java tool", "Java", "u", "main", "2020-01-01T00:00:00Z"),
    ]
    candidates = match_candidates(project_name="智能面试助手", tech_stack=["TypeScript", "Vue3"], repositories=repositories)
    assert candidates[0].repository.full_name == "alice/interview-copilot"
    assert candidates[0].score > candidates[1].score
    assert candidates[0].reasons


def test_store_upsert_link_and_state(tmp_path: Path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        resume = ResumeStore(connection).create(name="张三", file_name="r.docx", file_hash="h", raw_text="")
        project = ResumeStore(connection).add_project(
            resume.id,
            ResumeProject(
                id="rp_1",
                resume_id=resume.id,
                project_name="面试助手",
                confidence=0.6,
            ),
        )
        store = GitHubStore(connection)
        repo_id = store.upsert_repository(
            Repository("alice/repo", False, "desc", "Python", "u", "main", "2026-11-01T00:00:00Z")
        )
        store.link(project_id=project.id, repository_id=repo_id, score=0.8, reason="名称匹配", state="MATCHED")
        store.set_state(project_id=project.id, repository_id=repo_id, state="CONFIRMED")
        links = store.links(project.id)
        assert links[0]["state"] == "CONFIRMED"
        assert links[0]["full_name"] == "alice/repo"
    finally:
        close(connection)
