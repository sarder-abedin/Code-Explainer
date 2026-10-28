"""Per-language analyzers. Each fills in a FileInfo from its source text."""

from __future__ import annotations

from pathlib import PurePosixPath

from ..model import FileInfo
from . import generic, javascript, javascript_ts, python_ast

LANGUAGES = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".rb": "Ruby",
    ".php": "PHP", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".cs": "C#", ".swift": "Swift", ".scala": "Scala", ".sh": "Shell", ".bash": "Shell",
    ".md": "Markdown", ".rst": "Markdown", ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML", ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".sql": "SQL",
}
CODE_LANGS = {"Python", "JavaScript", "TypeScript"}


def detect_language(path: str) -> str:
    return LANGUAGES.get(PurePosixPath(path).suffix.lower(), "Other")


def analyze_file(path: str, lang: str, source: str) -> FileInfo:
    info = FileInfo(path=path, lang=lang)
    info.lines = source.count("\n") + (1 if source and not source.endswith("\n") else 0)
    info.loc = sum(1 for line in source.splitlines() if line.strip())
    if lang == "Python":
        python_ast.analyze(info, source)
    elif lang in ("JavaScript", "TypeScript"):
        if not javascript_ts.analyze(info, source):
            javascript.analyze(info, source)
    else:
        generic.analyze(info, source)
    return info
