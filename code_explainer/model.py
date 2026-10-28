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
            "symbols": [s.__dict__ for s in self.symbols],
            "imports": self.imports,
            "importedBy": self.imported_by,
            "entry": self.entry_reasons,
            "isTest": self.is_test,
            "unused": self.possibly_unused,
            "analyzed": self.analyzed,
            "parseError": self.parse_error,
            "source": self.source,
            "truncated": self.truncated,
        }
