"""RI-1 简历数据模型。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ResumeStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    FAILED = "failed"


class ResumeProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    resume_id: str
    project_name: str
    description: str = ""
    role: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    achievements: list[str] = Field(default_factory=list)
    github_urls: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    source_location: str = ""


class Resume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    file_name: str
    file_hash: str
    raw_text: str
    version: int = 1
    status: ResumeStatus = ResumeStatus.PARSED
    created_at: int
    updated_at: int
    projects: list[ResumeProject] = Field(default_factory=list)


class ResumeProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str | None = None
    description: str | None = None
    role: str | None = None
    tech_stack: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    achievements: list[str] | None = None
    github_urls: list[str] | None = None
