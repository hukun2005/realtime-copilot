"""RI-4 个人项目知识库。"""

from .ingest import chunks_from_report, chunks_from_resume, technical_chunk
from .store import KnowledgeChunk, KnowledgeStore

__all__ = [
    "KnowledgeChunk",
    "KnowledgeStore",
    "chunks_from_report",
    "chunks_from_resume",
    "technical_chunk",
]
