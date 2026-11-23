"""RI-2：GitHub 公开仓库读取与候选匹配。

只读取仓库元数据；不下载源码、不执行代码。私有仓库需要调用方显式提供 Token；
Token 不落库、不写日志。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

GITHUB_URL = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)


class GitHubError(RuntimeError):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def parse_github_url(url: str) -> tuple[str, str] | None:
    match = GITHUB_URL.search(url)
    if match is None:
        return None
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    return owner, repo


@dataclass
class Repository:
    full_name: str
    private: bool
    description: str
    language: str | None
    html_url: str
    default_branch: str
    pushed_at: str | None
    stars: int = 0


@dataclass
class Candidate:
    repository: Repository
    score: float
    reasons: list[str] = field(default_factory=list)


class GitHubClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(transport=transport, timeout=20.0)

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "realtime-copilot"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def get_repo(self, owner: str, repo: str) -> Repository:
        data = await self._get(f"/repos/{owner}/{repo}")
        return _repo(data)

    async def list_repos(self, *, per_page: int = 100, max_pages: int = 3) -> list[Repository]:
        repos: list[Repository] = []
        for page in range(1, max_pages + 1):
            data = await self._get("/user/repos", params={"per_page": per_page, "page": page, "sort": "pushed"})
            if not isinstance(data, list):
                raise GitHubError("invalid_response")
            repos.extend(_repo(item) for item in data)
            if len(data) < per_page:
                break
        return repos

    async def get_tree(self, owner: str, repo: str, ref: str) -> tuple[list[dict[str, object]], bool]:
        data = await self._get(f"/repos/{owner}/{repo}/git/trees/{ref}", params={"recursive": "1"})
        if not isinstance(data, dict):
            raise GitHubError("invalid_response")
        tree = data.get("tree")
        if not isinstance(tree, list):
            raise GitHubError("invalid_response")
        return [item for item in tree if isinstance(item, dict)], bool(data.get("truncated", False))

    async def get_file_text(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str,
        *,
        max_bytes: int = 200_000,
    ) -> str | None:
        data = await self._get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref})
        if not isinstance(data, dict) or data.get("type") != "file":
            return None
        if int(data.get("size") or 0) > max_bytes:
            return None
        encoded = data.get("content")
        if not isinstance(encoded, str):
            return None
        try:
            raw = base64.b64decode(encoded, validate=False)
        except Exception as error:
            raise GitHubError("invalid_content") from error
        if len(raw) > max_bytes:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    async def _get(self, path: str, params: dict[str, object] | None = None) -> object:
        try:
            response = await self._client.get(f"{self._base}{path}", headers=self._headers(), params=params)
        except httpx.TimeoutException as error:
            raise GitHubError("timeout") from error
        except httpx.HTTPError as error:
            raise GitHubError("network_error") from error
        if response.status_code == 401:
            raise GitHubError("unauthorized", 401)
        if response.status_code == 403:
            raise GitHubError("rate_limited_or_forbidden", 403)
        if response.status_code == 404:
            raise GitHubError("not_found", 404)
        if response.status_code == 429:
            raise GitHubError("rate_limited", 429)
        if response.status_code >= 400:
            raise GitHubError(f"http_{response.status_code}", response.status_code)
        return response.json()


def match_candidates(
    *,
    project_name: str,
    tech_stack: list[str],
    repositories: list[Repository],
    limit: int = 5,
) -> list[Candidate]:
    """名称/技术栈/描述/最近推送的显式加权；分数不是归属结论。"""
    name = _tokens(project_name)
    stack = {item.lower() for item in tech_stack if item.strip()}
    now = datetime.now(UTC)
    candidates: list[Candidate] = []
    for repo in repositories:
        repo_name = repo.full_name.split("/", 1)[-1]
        reasons: list[str] = []
        name_hit = len(name & _tokens(repo_name))
        name_score = min(1.0, name_hit / max(1, len(name))) if name else 0.0
        if name_score:
            reasons.append(f"名称词重合 {name_hit}")
        tech_score = 0.0
        if stack and repo.language and repo.language.lower() in stack:
            tech_score = 1.0
            reasons.append(f"语言匹配 {repo.language}")
        desc = (repo.description or "").lower()
        desc_score = 1.0 if any(term in desc for term in stack) else 0.0
        if desc_score:
            reasons.append("描述含技术栈")
        recent_score = _recent_score(repo.pushed_at, now)
        if recent_score > 0:
            reasons.append("近期有提交")
        score = round(0.45 * name_score + 0.25 * tech_score + 0.2 * desc_score + 0.1 * recent_score, 3)
        candidates.append(Candidate(repository=repo, score=score, reasons=reasons))
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:limit]


def _repo(data: object) -> Repository:
    if not isinstance(data, dict) or "full_name" not in data:
        raise GitHubError("invalid_response")
    return Repository(
        full_name=str(data["full_name"]),
        private=bool(data.get("private", False)),
        description=str(data.get("description") or ""),
        language=data.get("language"),
        html_url=str(data.get("html_url") or ""),
        default_branch=str(data.get("default_branch") or "main"),
        pushed_at=data.get("pushed_at"),
        stars=int(data.get("stargazers_count") or 0),
    )


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", text.lower()) if len(token) >= 2}


def _recent_score(pushed_at: str | None, now: datetime) -> float:
    if not pushed_at:
        return 0.0
    try:
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    days = (now - pushed).days
    if days <= 30:
        return 1.0
    if days <= 180:
        return 0.5
    return 0.0
