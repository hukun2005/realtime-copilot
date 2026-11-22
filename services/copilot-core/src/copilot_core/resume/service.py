"""RI-1：简历导入服务（校验 → 解析 → 入库）。"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from .extract import extract_text, sanitize_filename
from .models import Resume, ResumeProject
from .parser import parse_resume
from .store import ResumeStore


def ingest_resume(path: Path, *, file_name: str, store: ResumeStore) -> Resume:
    safe_name = sanitize_filename(file_name)
    text, _units = extract_text(path, safe_name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    parsed = parse_resume(text)
    resume = store.create(
        name=parsed.name,
        file_name=safe_name,
        file_hash=digest,
        raw_text=parsed.raw_text,
    )
    for item in parsed.projects:
        store.add_project(
            resume.id,
            ResumeProject(
                id="rp_" + uuid.uuid4().hex,
                resume_id=resume.id,
                project_name=item.project_name,
                description=item.description,
                role=item.role,
                tech_stack=item.tech_stack,
                start_date=item.start_date,
                end_date=item.end_date,
                achievements=item.achievements,
                github_urls=item.github_urls,
                confidence=item.confidence,
                source_location=item.source_location,
            ),
        )
    return store.get(resume.id) or resume
