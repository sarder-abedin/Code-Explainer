"""Python analysis through the standard library `ast` module."""

from __future__ import annotations

import ast

from ..model import FileInfo, Symbol

_DECISIONS = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp,
    ast.comprehension,
)


def _first_line(doc: str | None) -> str:
    if not doc:
        return ""
    line = doc.strip().splitlines()[0].strip()
    return line[:160]


def complexity(node: ast.AST) -> int:
    score = 1
    for n in ast.walk(node):
        if isinstance(n, _DECISIONS):
            score += 1
        elif isinstance(n, ast.BoolOp):
            score += len(n.values) - 1
        elif hasattr(ast, "match_case") and isinstance(n, ast.match_case):
            score += 1
    return score


def _is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    t = node.test
    names = [t.left, *t.comparators]
    has_name = any(isinstance(n, ast.Name) and n.id == "__name__" for n in names)
    has_main = any(isinstance(n, ast.Constant) and n.value == "__main__" for n in names)
    return has_name and has_main


def _collect(body: list[ast.stmt], parent: str | None, out: list[Symbol]) -> None:
    for node in body:
        if isinstance(node, ast.ClassDef):
            qual = f"{parent}.{node.name}" if parent else node.name
            out.append(Symbol(node.name, "class", node.lineno, node.end_lineno or node.lineno,
                              _first_line(ast.get_docstring(node)), complexity(node), parent))
            _collect(node.body, qual, out)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "method" if parent else "function"
            out.append(Symbol(node.name, kind, node.lineno, node.end_lineno or node.lineno,
                              _first_line(ast.get_docstring(node)), complexity(node), parent))


def analyze(info: FileInfo, source: str) -> None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as e:
        info.parse_error = str(e)
        return
    info.analyzed = True
    info.doc = _first_line(ast.get_docstring(tree))
    info.complexity = complexity(tree)
    _collect(tree.body, None, info.symbols)
    # Imports anywhere in the file, including function-local ones.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                info.raw_imports.append((0, alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            info.raw_imports.append((node.level, node.module or "", [a.name for a in node.names]))
    if any(_is_main_guard(n) for n in tree.body):
        info.entry_reasons.append("has an `if __name__ == \"__main__\"` block")
