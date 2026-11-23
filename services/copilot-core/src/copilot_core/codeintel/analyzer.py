"""RI-3：静态符号提取与证据化项目报告。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

PY_CLASS = re.compile(r"^class\s+(\w+)")
PY_DEF = re.compile(r"^(?:async\s+)?def\s+(\w+)")
JS_CLASS = re.compile(r"^(?:export\s+)?class\s+(\w+)")
JS_FUNC = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)")
JS_CONST = re.compile(r"^(?:export\s+)?const\s+(\w+)\s*=")
CS_CLASS = re.compile(r"^\s*(?:public|internal|private)?\s*(?:sealed\s+)?class\s+(\w+)")
CS_METHOD = re.compile(r"^\s*(?:public|private|internal|protected)?\s*(?:async\s+)?[\w<>\[\]]+\s+(\w+)\s*\(")
API_ROUTE = re.compile(r"(?:app|router|server)\.(get|post|put|patch|delete)\(['\"]([^'\"]+)")
STORE = re.compile(r"defineStore\(['\"]([^'\"]+)")
VUE_COMPONENT = re.compile(r"<script[^>]*setup")
MODEL = re.compile(r"^(?:class|interface)\s+(\w+)")
MD_HEADING = re.compile(r"^(#{1,3})\s+(.+)")


@dataclass
class Symbol:
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int
    summary: str


@dataclass
class AnalysisReport:
    repository: str
    commit_sha: str
    analysis_version: str
    overview: str
    tech_stack: list[str]
    structure: list[str]
    sections: dict[str, str]
    symbols: list[Symbol]
    api_routes: list[dict[str, object]]
    models: list[Symbol]
    evidence: list[dict[str, object]] = field(default_factory=list)
    partial: bool = False
    limitations: list[str] = field(default_factory=list)


def analyze_repository(
    *,
    repository: str,
    commit_sha: str,
    files: dict[str, str],
    partial: bool = False,
) -> AnalysisReport:
    symbols: list[Symbol] = []
    routes: list[dict[str, object]] = []
    models: list[Symbol] = []
    headings: list[str] = []
    manifests: list[str] = []

    for path, text in files.items():
        if path.lower().endswith((".json", ".toml", ".yml", ".yaml", ".md")):
            manifests.append(path)
        for number, line in enumerate(text.splitlines(), start=1):
            symbol = _symbol(path, number, line)
            if symbol is not None:
                symbols.append(symbol)
                if symbol.kind in ("class", "interface"):
                    models.append(symbol)
            for verb, route in API_ROUTE.findall(line):
                routes.append({"method": verb.upper(), "path": route, "file": path, "line": number})
            heading = MD_HEADING.match(line)
            if heading:
                headings.append(heading.group(2).strip())

    tech = _tech_stack(files)
    evidence = [
        {
            "repository": repository,
            "commit_sha": commit_sha,
            "file_path": symbol.path,
            "start_line": symbol.start_line,
            "end_line": symbol.end_line,
            "evidence_summary": symbol.summary,
        }
        for symbol in symbols[:50]
    ]
    sections = {
        "overview": _overview(tech, files, headings),
        "architecture": "基于目录与入口文件推断：" + ", ".join(sorted(set(_top_dirs(files)))[:8]) if files else "UNKNOWN",
        "core_flow": "UNKNOWN：需要运行时/调用图验证，本轮仅静态符号。",
        "api": f"发现 {len(routes)} 个路由" if routes else "UNKNOWN：未发现框架路由模式",
        "data_models": f"发现 {len(models)} 个类/接口" if models else "UNKNOWN",
        "state": "YES" if any(symbol.kind == "store" for symbol in symbols) else "UNKNOWN",
        "error_handling": "UNKNOWN：需结合异常路径分析",
        "tests": "存在测试文件" if any("test" in path.lower() for path in files) else "UNKNOWN：未发现测试文件",
        "choices": "INFERENCE：仅依据语言/框架文件，不做质量判断",
        "highlights": "INFERENCE：需人工确认，避免把建议当事实",
        "improvements": "UNKNOWN",
        "interview_questions": _questions(tech, routes, models),
    }
    return AnalysisReport(
        repository=repository,
        commit_sha=commit_sha,
        analysis_version="ri3-v1",
        overview=sections["overview"],
        tech_stack=tech,
        structure=sorted(files)[:80],
        sections=sections,
        symbols=symbols,
        api_routes=routes,
        models=models,
        evidence=evidence,
        partial=partial,
        limitations=["未执行代码", "未构建完整调用图", "未覆盖私有仓库/大仓库全量"],
    )


def _symbol(path: str, line: int, text: str) -> Symbol | None:
    suffix = path.lower().rsplit(".", 1)[-1]
    patterns: list[tuple[re.Pattern[str], str]] = []
    if suffix == "py":
        patterns = [(PY_CLASS, "class"), (PY_DEF, "function")]
    elif suffix in {"ts", "tsx", "js", "jsx"}:
        patterns = [(JS_CLASS, "class"), (JS_FUNC, "function"), (JS_CONST, "const")]
    elif suffix == "cs":
        patterns = [(CS_CLASS, "class"), (CS_METHOD, "method")]
    elif suffix == "vue" and VUE_COMPONENT.search(text):
        return Symbol("<component>", "component", path, line, line, f"Vue component {path}")
    if STORE.search(text):
        return Symbol(STORE.search(text).group(1), "store", path, line, line, "Pinia store")
    for pattern, kind in patterns:
        match = pattern.search(text)
        if match:
            return Symbol(match.group(1), kind, path, line, line, f"{kind} {match.group(1)}")
    return None


def _tech_stack(files: dict[str, str]) -> list[str]:
    stack: set[str] = set()
    if any(path.endswith(".vue") for path in files):
        stack.add("Vue3")
    if any(path.endswith((".ts", ".tsx")) for path in files):
        stack.add("TypeScript")
    if any(path.endswith(".py") for path in files):
        stack.add("Python")
    if any(path.endswith(".cs") for path in files):
        stack.add("C#")
    package = files.get("package.json")
    if package:
        try:
            data = json.loads(package)
            for key in ("dependencies", "devDependencies"):
                stack.update((data.get(key) or {}).keys())
        except json.JSONDecodeError:
            pass
    return sorted(stack)


def _overview(tech: list[str], files: dict[str, str], headings: list[str]) -> str:
    if not files:
        return "UNKNOWN：没有可分析文件"
    readme = next((text for path, text in files.items() if path.lower().startswith("readme")), "")
    first = next((line.strip("# ") for line in readme.splitlines() if line.strip()), "")
    return first or f"{len(files)} 个文件，技术栈：{', '.join(tech) or 'UNKNOWN'}"


def _top_dirs(files: dict[str, str]) -> list[str]:
    return [path.split("/", 1)[0] for path in files if "/" in path]


def _questions(tech: list[str], routes: list[dict[str, object]], models: list[Symbol]) -> str:
    items = []
    if tech:
        items.append(f"为什么选择 {tech[0]}？")
    if routes:
        items.append("接口如何做鉴权与错误处理？")
    if models:
        items.append("核心数据模型如何演进？")
    return "；".join(items) or "UNKNOWN"
