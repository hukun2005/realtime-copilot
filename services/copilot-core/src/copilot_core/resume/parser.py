"""RI-1：规则化简历项目提取（保留原文与行号）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PROJECT_HEADERS = ("项目经历", "项目经验", "projects", "project experience")
OTHER_HEADERS = (
    "教育", "工作经历", "实习经历", "技能", "专业技能", "荣誉", "证书", "自我评价",
    "education", "experience", "skills", "awards",
)
BULLET = re.compile(r"^\s*[-*•·▪◦]\s*")
DATE_RANGE = re.compile(
    r"(20\d{2}[./年-]\d{1,2}月?)\s*[-–—~至到]\s*(20\d{2}[./年-]\d{1,2}月?|至今|present)",
    re.I,
)
GITHUB_URL = re.compile(r"https?://github\.com/[\w.-]+(?:/[\w.-]+)?", re.I)
TECH_TERMS = (
    "Vue", "Vue2", "Vue3", "React", "TypeScript", "JavaScript", "Node.js", "Electron",
    "Pinia", "Vuex", "Python", "FastAPI", "Django", "Flask", "Java", "Spring", "Go",
    "C#", ".NET", "SQLite", "PostgreSQL", "MySQL", "Redis", "Docker", "Kubernetes",
    "AWS", "GitHub Actions", "WebSocket", "RAG", "LLM",
)
ACHIEVEMENT_MARKERS = ("负责", "实现", "优化", "提升", "主导", "设计", "上线", "落地", "降低", "支持")


@dataclass
class ParsedProject:
    project_name: str
    description: str = ""
    role: str = ""
    tech_stack: list[str] = field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    achievements: list[str] = field(default_factory=list)
    github_urls: list[str] = field(default_factory=list)
    confidence: float = 0.5
    source_location: str = ""


@dataclass
class ParsedResume:
    name: str
    projects: list[ParsedProject]
    github_urls: list[str]
    raw_text: str


def parse_resume(text: str) -> ParsedResume:
    lines = [line.rstrip() for line in text.splitlines()]
    name = _extract_name(lines)
    projects = _projects_from_sections(lines)
    if not projects:
        projects = _projects_from_github(lines)
    all_urls = sorted({url for project in projects for url in project.github_urls})
    return ParsedResume(name=name, projects=projects, github_urls=all_urls, raw_text=text)


def _clean(line: str) -> str:
    line = re.sub(r"^\[(page|para) \d+\]\s*", "", line)
    return line.strip()


def _extract_name(lines: list[str]) -> str:
    for line in lines:
        clean = _clean(line)
        if not clean or clean.startswith("["):
            continue
        if len(clean) <= 30 and not GITHUB_URL.search(clean):
            return clean.replace("个人简历", "").strip() or clean
    return "未命名简历"


def _is_header(clean: str, headers: tuple[str, ...] = PROJECT_HEADERS) -> bool:
    lowered = clean.lower().strip(":： ")
    return any(header.lower() in lowered for header in headers) and len(clean) <= 30


def _projects_from_sections(lines: list[str]) -> list[ParsedProject]:
    start = None
    for index, line in enumerate(lines):
        if _is_header(_clean(line)):
            start = index + 1
            break
    if start is None:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        clean = _clean(lines[index])
        if clean and _is_header(clean, OTHER_HEADERS) and not _is_header(clean):
            end = index
            break

    projects: list[ParsedProject] = []
    current: ParsedProject | None = None
    block_start = start
    for index in range(start, end):
        clean = _clean(lines[index])
        if not clean:
            continue
        if _looks_like_title(clean) and not BULLET.match(clean):
            if current is not None:
                current.source_location = f"line {block_start + 1}-{index}"
                projects.append(current)
            current = ParsedProject(project_name=_title_only(clean), confidence=0.65)
            block_start = index
            _fill_dates(current, clean)
            continue
        if current is None:
            continue
        _consume_line(current, clean)
    if current is not None:
        current.source_location = f"line {block_start + 1}-{end}"
        projects.append(current)
    return [project for project in projects if project.project_name]


def _looks_like_title(clean: str) -> bool:
    if len(clean) > 60 or clean.endswith("。"):
        return False
    return not (_is_header(clean) or _is_header(clean, OTHER_HEADERS))


def _title_only(clean: str) -> str:
    return DATE_RANGE.sub("", clean).strip(" -–—|")[:80]


def _fill_dates(project: ParsedProject, clean: str) -> None:
    match = DATE_RANGE.search(clean)
    if match:
        project.start_date = match.group(1)
        project.end_date = match.group(2)


def _consume_line(project: ParsedProject, clean: str) -> None:
    for url in GITHUB_URL.findall(clean):
        if url not in project.github_urls:
            project.github_urls.append(url)
    for term in TECH_TERMS:
        if term.lower() in clean.lower() and term not in project.tech_stack:
            project.tech_stack.append(term)
    if DATE_RANGE.search(clean) and project.start_date is None:
        _fill_dates(project, clean)
    text = BULLET.sub("", clean).strip()
    if not text:
        return
    if any(marker in text for marker in ACHIEVEMENT_MARKERS):
        project.achievements.append(text)
    project.description = (project.description + " " + text).strip()
    if "负责" in text or "担任" in text:
        project.role = text[:80]


def _projects_from_github(lines: list[str]) -> list[ParsedProject]:
    projects: list[ParsedProject] = []
    for index, line in enumerate(lines):
        clean = _clean(line)
        for url in GITHUB_URL.findall(clean):
            title = ""
            for previous in range(index - 1, max(-1, index - 4), -1):
                candidate = _clean(lines[previous])
                if candidate and not GITHUB_URL.search(candidate):
                    title = _title_only(candidate)
                    break
            projects.append(
                ParsedProject(
                    project_name=title or url.rsplit("/", 1)[-1],
                    description=clean,
                    github_urls=[url],
                    confidence=0.4,
                    source_location=f"line {index + 1}",
                )
            )
    return projects
