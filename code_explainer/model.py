"""Language-neutral data model shared by every analyzer and the report."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Symbol:
    name: str
    kind: str  # "class" | "function" | "method"
    line: int
    end_line: int
    doc: str = ""
    complexity: int = 1
    parent: str | None = None
    qual: str = ""  # "Class.method" or "name"; "<module>" for top-level code
    entry: str = ""  # why this symbol starts a flow (route, CLI command, ...)
    # Raw call sites before resolution: ([name, attr, ...], line)
    raw_calls: list = field(default_factory=list)
    # Resolved: [{"id": "path::qual", "conf": "certain"|"inferred", "line": int}]
    callees: list = field(default_factory=list)
    explain: dict = field(default_factory=dict)  # {"kid": str, "plain": str}

    def __post_init__(self):
        if not self.qual:
            self.qual = f"{self.parent}.{self.name}" if self.parent else self.name

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "line": self.line, "end_line": self.end_line,
            "doc": self.doc, "complexity": self.complexity, "parent": self.parent,
            "qual": self.qual, "entry": self.entry, "callees": self.callees, "explain": self.explain,
        }


@dataclass
class FileInfo:
    path: str  # posix, relative to repo root
    lang: str
    size: int = 0
    lines: int = 0
    loc: int = 0  # non-blank lines
    complexity: int = 0
    churn: int = 0  # number of commits touching the file
    doc: str = ""
    symbols: list[Symbol] = field(default_factory=list)
    # Analyzer output before resolution; shape is analyzer specific.
    raw_imports: list = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    imported_by: list[str] = field(default_factory=list)
    entry_reasons: list[str] = field(default_factory=list)
    is_test: bool = False
    possibly_unused: bool = False
    analyzed: bool = False  # True when a real parser (not just line counting) ran
    analyzer: str = ""  # "ast" | "tree-sitter" | "regex" | ""
    has_calls: bool = False  # True when call sites were extracted
    # local name -> binding tuple (python: (level, module, attr|None); js: (spec, attr))
    bindings: dict = field(default_factory=dict)
    default_export: str = ""
    explain: dict = field(default_factory=dict)
    parse_error: str = ""
    source: str = ""
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "lang": self.lang,
            "size": self.size,
            "lines": self.lines,
            "loc": self.loc,
            "complexity": self.complexity,
            "churn": self.churn,
            "doc": self.doc,
            "explain": self.explain,
            "symbols": [s.to_dict() for s in self.symbols],
            "imports": self.imports,
            "importedBy": self.imported_by,
            "entry": self.entry_reasons,
            "isTest": self.is_test,
            "unused": self.possibly_unused,
            "analyzed": self.analyzed,
            "analyzer": self.analyzer,
            "hasCalls": self.has_calls,
            "parseError": self.parse_error,
            "source": self.source,
            "truncated": self.truncated,
        }
