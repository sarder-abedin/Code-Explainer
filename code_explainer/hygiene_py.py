"""Python patterns worth a second look, found from the syntax tree (so strings and comments never match)."""

from __future__ import annotations

import ast

_SUBPROCESS = {"run", "call", "Popen", "check_output", "check_call", "getoutput", "getstatusoutput"}
_PICKLES = {("pickle", "load"), ("pickle", "loads"), ("cPickle", "load"), ("cPickle", "loads"), ("dill", "load"),
            ("dill", "loads"), ("marshal", "load"), ("marshal", "loads")}
_SQL_METHODS = {"execute", "executemany", "executescript"}


def _is_str(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _dotted(func) -> tuple[str, str] | None:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id, func.attr
    return None


def _built_string(node) -> bool:
    """A string assembled at run time: an f-string with values, `"..." + x`, `"..." % x` or `"...".format(x)`."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _is_str(node.left) or _is_str(node.right) or _built_string(node.left) or _built_string(node.right)
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"
            and (_is_str(node.func.value) or isinstance(node.func.value, ast.JoinedStr)))


def _keyword(call: ast.Call, name: str):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_true(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_false(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _call_hits(node: ast.Call):
    func = node.func
    if isinstance(func, ast.Name) and func.id in ("eval", "exec"):
        yield f"py-{func.id}", "high"
    dotted = _dotted(func)
    if dotted:
        mod, attr = dotted
        first = node.args[0] if node.args else None
        if mod == "subprocess" and attr in _SUBPROCESS and _is_true(_keyword(node, "shell")):
            yield "shell-injection", "medium" if _is_str(first) else "high"
        elif mod == "os" and attr in ("system", "popen"):
            yield "shell-injection", "medium" if _is_str(first) else "high"
        elif (mod, attr) in _PICKLES:
            yield "py-pickle", "medium"
        elif mod == "yaml" and attr == "load":
            loader = _keyword(node, "Loader") or (node.args[1] if len(node.args) > 1 else None)
            name = (loader.attr if isinstance(loader, ast.Attribute) else loader.id if isinstance(loader, ast.Name) else "")
            if "Safe" not in name:
                yield "py-yaml-load", "medium"
        elif mod == "hashlib" and attr in ("md5", "sha1"):
            yield "weak-hash", "low"
        elif mod == "tempfile" and attr == "mktemp":
            yield "py-mktemp", "medium"
    if isinstance(func, ast.Attribute):
        if func.attr in _SQL_METHODS and node.args and _built_string(node.args[0]):
            yield "sql-string", "high"
        if func.attr == "run" and _is_true(_keyword(node, "debug")):
            yield "debug-on", "medium"
    if _is_false(_keyword(node, "verify")):
        yield "tls-verify-off", "medium"


def _only_passes(body) -> bool:
    return all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis)
               for s in body)


def _handler_hits(node: ast.ExceptHandler):
    if node.type is None:
        yield "bare-except", "low"
    elif isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException") and _only_passes(node.body):
        yield "swallowed-error", "low"


def scan(source: str) -> list[tuple[str, str, int]]:
    """[(rule id, severity, line)]; empty when the file does not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            hits += [(rule, sev, node.lineno) for rule, sev in _call_hits(node)]
        elif isinstance(node, ast.ExceptHandler):
            hits += [(rule, sev, node.lineno) for rule, sev in _handler_hits(node)]
    return hits
