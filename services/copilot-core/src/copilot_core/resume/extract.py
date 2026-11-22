"""RI-1：PDF / DOCX 安全读取。

只提取文本，不执行文档脚本；限制大小、页数、ZIP 条目与解压总量。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from docx import Document
from pypdf import PdfReader

MAX_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 30
MAX_DOCX_ENTRIES = 1000
MAX_UNCOMPRESSED = 30 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx"}
EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class ResumeFileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def sanitize_filename(name: str) -> str:
    base = Path(name).name
    base = CONTROL_CHARS.sub("", base).strip().lstrip(".")
    if not base:
        raise ResumeFileError("invalid_filename", "empty file name")
    if len(base) > 120:
        base = base[:120]
    return base


def validate_file(path: Path, file_name: str) -> str:
    safe_name = sanitize_filename(file_name)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ResumeFileError("unsupported_type", "only PDF and DOCX are supported")
    size = path.stat().st_size
    if size == 0:
        raise ResumeFileError("empty_file", "file is empty")
    if size > MAX_BYTES:
        raise ResumeFileError("file_too_large", "file exceeds 5 MiB")
    head = path.read_bytes()[:8]
    if head.startswith(EXECUTABLE_MAGIC):
        raise ResumeFileError("executable_file", "executable files are rejected")
    if suffix == ".pdf" and not head.startswith(b"%PDF-"):
        raise ResumeFileError("invalid_pdf", "missing PDF signature")
    if suffix == ".docx" and not head.startswith(b"PK\x03\x04"):
        raise ResumeFileError("invalid_docx", "missing DOCX/ZIP signature")
    return safe_name


def extract_text(path: Path, file_name: str) -> tuple[str, int]:
    """返回 (原始文本, 页数/段落数)。"""
    validate_file(path, file_name)
    if path.suffix.lower() == ".pdf":
        return _extract_pdf(path)
    return _extract_docx(path)


def _extract_pdf(path: Path) -> tuple[str, int]:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ResumeFileError("encrypted_pdf", "encrypted PDF is not supported")
        pages = len(reader.pages)
        if pages > MAX_PDF_PAGES:
            raise ResumeFileError("too_many_pages", "PDF exceeds 30 pages")
        chunks = []
        for index, page in enumerate(reader.pages, start=1):
            chunks.append(f"[page {index}]\n{page.extract_text() or ''}")
        return "\n".join(chunks), pages
    except ResumeFileError:
        raise
    except Exception as error:
        raise ResumeFileError("parse_failed", "cannot parse PDF") from error


def _extract_docx(path: Path) -> tuple[str, int]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES:
                raise ResumeFileError("too_many_entries", "DOCX has too many entries")
            total = sum(item.file_size for item in entries)
            if total > MAX_UNCOMPRESSED:
                raise ResumeFileError("zip_bomb", "DOCX uncompressed size exceeds limit")
            if any("vbaproject.bin" in item.filename.lower() for item in entries):
                raise ResumeFileError("macro_document", "macro-enabled documents are rejected")
    try:
        document = Document(str(path))
        lines = [f"[para {index}] {paragraph.text}" for index, paragraph in enumerate(document.paragraphs, start=1)]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))
        return "\n".join(lines), len(lines)
    except ResumeFileError:
        raise
    except Exception as error:
        raise ResumeFileError("parse_failed", "cannot parse DOCX") from error
