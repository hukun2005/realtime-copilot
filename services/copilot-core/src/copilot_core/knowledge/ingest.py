"""RI-4：把简历项目、代码符号、分析报告转成知识块。"""

from __future__ import annotations

import hashlib

from ..codeintel import AnalysisReport
from ..resume import Resume
from .store import KnowledgeChunk


def _chunk_id(*parts: object) -> str:
    return "kc_" + hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()[:20]


def chunks_from_resume(resume: Resume) -> list[KnowledgeChunk]:
    chunks = []
    for project in resume.projects:
        content = "\n".join(
            part
            for part in (
                project.project_name,
                f"角色: {project.role}" if project.role else "",
                project.description,
                "技术栈: " + ", ".join(project.tech_stack) if project.tech_stack else "",
                "成果: " + "；".join(project.achievements) if project.achievements else "",
                "GitHub: " + ", ".join(project.github_urls) if project.github_urls else "",
            )
            if part
        )
        chunks.append(
            KnowledgeChunk(
                chunk_id=_chunk_id(resume.id, project.id),
                project_id=project.id,
                repository_id="",
                commit_sha="",
                source_type="resume",
                file_path="",
                language="",
                symbol_name=project.project_name,
                start_line=0,
                end_line=0,
                content=content,
            )
        )
    return chunks


def chunks_from_report(report: AnalysisReport, *, project_id: str, repository_id: str) -> list[KnowledgeChunk]:
    chunks = []
    for symbol in report.symbols:
        chunks.append(
            KnowledgeChunk(
                chunk_id=_chunk_id(repository_id, report.commit_sha, symbol.path, symbol.start_line, symbol.name),
                project_id=project_id,
                repository_id=repository_id,
                commit_sha=report.commit_sha,
                source_type="analysis",
                file_path=symbol.path,
                language=symbol.path.rsplit(".", 1)[-1],
                symbol_name=symbol.name,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                content=f"{symbol.kind} {symbol.name}: {symbol.summary}",
            )
        )
    for route in report.api_routes:
        chunks.append(
            KnowledgeChunk(
                chunk_id=_chunk_id(repository_id, report.commit_sha, route["file"], route["line"], route["path"]),
                project_id=project_id,
                repository_id=repository_id,
                commit_sha=report.commit_sha,
                source_type="analysis",
                file_path=str(route["file"]),
                language="",
                symbol_name=f"{route['method']} {route['path']}",
                start_line=int(route["line"]),
                end_line=int(route["line"]),
                content=f"API {route['method']} {route['path']}",
            )
        )
    return chunks


def technical_chunk(*, topic: str, content: str, project_id: str = "", repository_id: str = "") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=_chunk_id("technical", topic, content),
        project_id=project_id,
        repository_id=repository_id,
        commit_sha="",
        source_type="technical",
        file_path="",
        language="",
        symbol_name=topic,
        start_line=0,
        end_line=0,
        content=content,
    )
