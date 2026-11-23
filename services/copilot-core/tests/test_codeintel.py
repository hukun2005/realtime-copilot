"""RI-3：Tree 筛选、敏感排除、静态分析与证据。"""

from __future__ import annotations

import base64

import httpx
from copilot_core.codeintel import CodeIntelStore, analyze_repository, is_sensitive, select_files
from copilot_core.github import GitHubClient, GitHubStore, Repository
from copilot_core.storage import close, open_database


def tree() -> list[dict[str, object]]:
    return [
        {"path": "README.md", "type": "blob", "size": 100},
        {"path": "package.json", "type": "blob", "size": 100},
        {"path": "src/main.ts", "type": "blob", "size": 100},
        {"path": "src/store/user.ts", "type": "blob", "size": 100},
        {"path": "tests/main.test.ts", "type": "blob", "size": 100},
        {"path": ".env", "type": "blob", "size": 10},
        {"path": "keys/id_rsa", "type": "blob", "size": 10},
        {"path": "node_modules/x.js", "type": "blob", "size": 10},
        {"path": "assets/logo.png", "type": "blob", "size": 10},
    ]


def test_filters_sensitive_and_ignored() -> None:
    result = select_files(tree(), tree_truncated=True)
    paths = [item.path for item in result.files]
    assert "src/main.ts" in paths and "README.md" in paths
    assert ".env" not in paths and "keys/id_rsa" not in paths
    assert "node_modules/x.js" not in paths and "assets/logo.png" not in paths
    assert result.skipped_sensitive == 2
    assert result.truncated is True


def test_sensitive_patterns() -> None:
    assert is_sensitive("a/.env.production")
    assert is_sensitive("cert/server.pem")
    assert not is_sensitive("src/main.ts")


async def test_tree_and_file_fetch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/git/trees/" in request.url.path:
            return httpx.Response(200, json={"tree": tree(), "truncated": False})
        encoded = base64.b64encode(b"export function hello() {}\n").decode()
        return httpx.Response(200, json={"type": "file", "size": 26, "content": encoded})

    client = GitHubClient(transport=httpx.MockTransport(handler))
    try:
        items, truncated = await client.get_tree("a", "b", "main")
        text = await client.get_file_text("a", "b", "src/main.ts", "main")
    finally:
        await client.close()
    assert items and truncated is False
    assert text is not None and "hello" in text


async def test_file_size_limit_returns_none() -> None:
    encoded = base64.b64encode(b"x" * 1000).decode()
    client = GitHubClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"type": "file", "size": 1000, "content": encoded})
        )
    )
    try:
        assert await client.get_file_text("a", "b", "big.ts", "main", max_bytes=10) is None
    finally:
        await client.close()


def test_analysis_has_evidence_and_partial() -> None:
    report = analyze_repository(
        repository="alice/demo",
        commit_sha="abc123",
        files={
            "README.md": "# Demo\nA Vue3 app\n",
            "src/main.ts": "export function hello() {}\nclass User {}\n",
            "src/api.ts": "router.get('/users', handler)\n",
            "src/store/user.ts": "defineStore('user', {})\n",
        },
        partial=True,
    )
    assert report.repository == "alice/demo" and report.commit_sha == "abc123"
    assert report.partial is True
    assert report.evidence
    first = report.evidence[0]
    assert first["file_path"] and first["start_line"] >= 1
    assert report.tech_stack
    assert report.api_routes[0]["method"] == "GET"
    assert report.models
    assert "UNKNOWN" in report.sections["core_flow"]


def test_analysis_empty_repository_is_unknown() -> None:
    report = analyze_repository(repository="a/b", commit_sha="x", files={})
    assert report.overview.startswith("UNKNOWN")
    assert report.evidence == []


def test_report_versions_are_persisted(tmp_path) -> None:
    connection = open_database(tmp_path / "copilot.db")
    try:
        repo_id = GitHubStore(connection).upsert_repository(
            Repository("a/b", False, "demo", "Python", "u", "main", "2026-11-01T00:00:00Z")
        )
        store = CodeIntelStore(connection)
        store.save_snapshot(repository_id=repo_id, commit_sha="abc", partial=False, file_count=2)
        report = analyze_repository(
            repository="a/b",
            commit_sha="abc",
            files={"main.py": "def hello():\n    pass\n"},
        )
        store.save_report(repository_id=repo_id, report=report)
        latest = store.latest(repo_id)
        assert latest is not None
        assert latest["commit_sha"] == "abc"
    finally:
        close(connection)
