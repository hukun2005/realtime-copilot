"""RI-3：仓库文件筛选（不下载秘密文件、不执行代码）。"""

from __future__ import annotations

from dataclasses import dataclass

IGNORE_DIRS = {
    ".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__",
    "vendor", "target", "bin", "obj", "coverage", ".next", ".nuxt",
}
IGNORE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".mp4", ".mp3",
    ".wav", ".pdf", ".zip", ".gz", ".tar", ".7z", ".exe", ".dll", ".so",
    ".dylib", ".class", ".pyc", ".woff", ".woff2", ".ttf", ".map",
}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "credentials", "credentials.json",
    "id_rsa", "id_ed25519", "secrets.json", "secrets.yml", "secrets.yaml",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".sqlite", ".db", ".bak"}
ALLOWED_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".cs", ".json", ".md",
    ".toml", ".yaml", ".yml", ".txt", ".csproj", ".sln", ".cfg", ".ini",
    ".dockerfile", ".go", ".rs",
}
PRIORITY = (
    "readme", "package.json", "pyproject.toml", "requirements.txt", "go.mod",
    "cargo.toml", "dockerfile", ".csproj", "app.py", "main.py", "main.ts",
)
MAX_FILES = 200
MAX_FILE_BYTES = 200_000


@dataclass
class FileRef:
    path: str
    size: int


@dataclass
class ScanResult:
    files: list[FileRef]
    skipped_sensitive: int
    skipped_large: int
    skipped_ignored: int
    truncated: bool


def is_sensitive(path: str) -> bool:
    parts = [part.lower() for part in path.split("/")]
    name = parts[-1] if parts else ""
    if name in SENSITIVE_NAMES or any(name.startswith(f"{item}.") for item in (".env", "id_rsa", "id_ed25519")):
        return True
    return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)


def is_ignored(path: str) -> bool:
    parts = [part.lower() for part in path.split("/")]
    if any(part in IGNORE_DIRS for part in parts[:-1]):
        return True
    return any(path.lower().endswith(suffix) for suffix in IGNORE_SUFFIXES)


def select_files(tree: list[dict[str, object]], *, tree_truncated: bool = False) -> ScanResult:
    files: list[FileRef] = []
    sensitive = large = ignored = 0
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        path = str(entry.get("path") or "")
        size = int(entry.get("size") or 0)
        if not path:
            continue
        if is_sensitive(path):
            sensitive += 1
            continue
        if is_ignored(path) or not _allowed(path):
            ignored += 1
            continue
        if size > MAX_FILE_BYTES:
            large += 1
            continue
        files.append(FileRef(path, size))
    files.sort(key=lambda item: (_priority(item.path), item.path))
    truncated = tree_truncated or len(files) > MAX_FILES
    return ScanResult(files[:MAX_FILES], sensitive, large, ignored, truncated)


def _allowed(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if name.startswith("dockerfile") or name.endswith((".csproj", ".sln")):
        return True
    return any(lowered.endswith(suffix) for suffix in ALLOWED_SUFFIXES)


def _priority(path: str) -> int:
    lowered = path.lower()
    for index, token in enumerate(PRIORITY):
        if token in lowered:
            return index
    if "/test" in lowered or "spec" in lowered:
        return 50
    return 20
