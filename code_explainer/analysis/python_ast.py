"""Python analysis through the standard library `ast` module."""

from __future__ import annotations

import ast

from .. import flowgraph
from ..model import FileInfo, Symbol

_DECISIONS = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp,
    ast.comprehension,
)
# Decorator names that mean "the framework calls this for you".
_ROUTE_ATTRS = {"route", "get", "post", "put", "delete", "patch", "websocket", "head", "options"}
_COMMAND_ATTRS = {"command", "group", "task", "callback", "on_event", "listener", "receiver", "shared_task"}
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _first_line(doc: str | None) -> str:
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()[:160]


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


def _chain(node: ast.AST) -> list[str]:
    """Dotted callee as a list of names. Unknown receivers become '?'."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return _chain(node.value) + [node.attr]
    if isinstance(node, ast.Call):
        return ["?"]
    return ["?"]


def _calls_in(nodes: list[ast.AST], skip_defs: bool) -> list[tuple[list[str], int]]:
    """Call sites under `nodes`, in the order they run. With skip_defs, function
    bodies are not entered (their calls belong to the function), but decorators
    and defaults are."""
    found: list[tuple[int, int, list[str], int]] = []
    stack = list(nodes)
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Call):
            chain = _chain(n.func)
            if chain != ["?"]:
                # An inner call (`get()` in `get().x()`, or `b()` in `a(b())`) ends before the
                # call that uses its result, so ordering by end position gives execution order.
                found.append((n.end_lineno or n.lineno, n.end_col_offset or 0, chain, n.lineno))
        if skip_defs and isinstance(n, _FUNCS):
            stack.extend(n.decorator_list)
            stack.extend(n.args.defaults + [d for d in n.args.kw_defaults if d])
            continue
        stack.extend(ast.iter_child_nodes(n))
    found.sort(key=lambda c: (c[0], c[1]))
    seen, out = set(), []
    for _el, _ec, chain, line in found:
        key = (tuple(chain), line)
        if key not in seen:
            seen.add(key)
            out.append((chain, line))
    return out


def _entry_from_decorators(node: ast.AST) -> str:
    for d in getattr(node, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        if isinstance(target, ast.Attribute):
            if target.attr in _ROUTE_ATTRS and isinstance(d, ast.Call):
                return f"HTTP route (@{ast.unparse(target)})"
            if target.attr in _COMMAND_ATTRS:
                return f"registered handler (@{ast.unparse(target)})"
        elif isinstance(target, ast.Name) and target.id in _COMMAND_ATTRS:
            return f"registered handler (@{target.id})"
    return ""


def _collect(body: list[ast.stmt], parent: str | None, out: list[Symbol], lines: list[str]) -> None:
    for node in body:
        if isinstance(node, ast.ClassDef):
            sym = Symbol(node.name, "class", node.lineno, node.end_lineno or node.lineno,
                         _first_line(ast.get_docstring(node)), complexity(node), parent)
            out.append(sym)
            _collect(node.body, sym.qual, out, lines)
        elif isinstance(node, _FUNCS):
            kind = "method" if parent else "function"
            sym = Symbol(node.name, kind, node.lineno, node.end_lineno or node.lineno,
                         _first_line(ast.get_docstring(node)), complexity(node), parent)
            sym.raw_calls = _calls_in(node.body, skip_defs=False)
            sym.entry = _entry_from_decorators(node)
            try:
                graph = flowgraph.build_python(node, lines)
                sym.flow = flowgraph.finalize(graph) if graph else None
            except RecursionError:   # absurdly deep nesting: no chart, but the analysis carries on
                sym.flow = None
            out.append(sym)


def analyze(info: FileInfo, source: str) -> None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as e:
        info.parse_error = str(e)
        return
    info.analyzed = True
    info.analyzer = "ast"
    info.has_calls = True
    info.doc = _first_line(ast.get_docstring(tree))
    info.complexity = complexity(tree)
    _collect(tree.body, None, info.symbols, source.splitlines())

    # Top-level code (including the `__main__` guard and decorators) acts as a
    # pseudo-symbol so scripts have something to start a flow from.
    top = _calls_in(tree.body, skip_defs=True)
    if top:
        mod = Symbol("<module>", "module", 1, len(source.splitlines()) or 1, info.doc, 1, None, qual="<module>")
        mod.raw_calls = top
        info.symbols.insert(0, mod)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                info.raw_imports.append((0, alias.name, []))
                if alias.asname:
                    info.bindings[alias.asname] = (0, alias.name, None)
                else:
                    top_name = alias.name.split(".")[0]
                    info.bindings.setdefault(top_name, (0, top_name, None))
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            info.raw_imports.append((node.level, node.module or "", names))
            for a in node.names:
                if a.name != "*":
                    info.bindings[a.asname or a.name] = (node.level, node.module or "", a.name)
    if any(_is_main_guard(n) for n in tree.body):
        info.entry_reasons.append("has an `if __name__ == \"__main__\"` block")
