"""JavaScript / TypeScript analysis.

Regex based for now (Phase 2 replaces this with tree-sitter). It is deliberately
conservative: it finds top-level-looking declarations and relative imports, and
anything it cannot be sure about it leaves out.
"""

from __future__ import annotations

import re

from ..model import FileInfo, Symbol

_IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*|\brequire\s*\(\s*|\bimport\s*\(\s*)(['"])([^'"\n]+)\1"""
)
_FUNC = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)")
_CLASS = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")
_ARROW = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*"
    r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>"
)
_DIVIDER = re.compile(r"^[-=*#~_/\s]{3,}|[-=*#~_\s]{3,}$")
_BRANCH = re.compile(r"\b(?:if|for|while|case|catch)\b|&&|\|\||\?\?|(?<![?.])\?(?![.?:])")


def _clean(text: str) -> str:
    text = text.strip()
    return "" if _DIVIDER.search(text) else text[:160]


def _doc_above(lines: list[str], idx: int) -> str:
    if idx == 0:
        return ""
    prev = lines[idx - 1].strip()
    if prev.startswith("//"):
        return _clean(prev.lstrip("/ "))
    if prev.endswith("*/"):
        for j in range(idx - 1, max(idx - 15, -1), -1):
            text = lines[j].strip().strip("/*").strip()
            if text and not text.startswith("@"):
                return _clean(text)
    return ""


def analyze(info: FileInfo, source: str) -> None:
    info.analyzed = True
    info.analyzer = "regex"
    lines = source.splitlines()
    info.complexity = len(_BRANCH.findall(source))
    for m in _IMPORT.finditer(source):
        info.raw_imports.append(m.group(2))
    starts: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        for rx, kind in ((_CLASS, "class"), (_FUNC, "function"), (_ARROW, "function")):
            m = rx.match(line)
            if m:
                starts.append((i, m.group(1), kind))
                break
    for n, (i, name, kind) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        body = "\n".join(lines[i:end])
        info.symbols.append(Symbol(name, kind, i + 1, max(i + 1, end),
                                   _doc_above(lines, i), 1 + len(_BRANCH.findall(body))))
