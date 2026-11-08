"""The contract of a function: what it takes, hands back, can fail with, changes and touches.

Everything here is read from the syntax tree and the call graph, never guessed by a model. Effects that can only be
guessed from names (a method on an unknown object) are left out, and the page labels the rest as "looks like".

Per function (`Symbol.contract`):
  method / async   booleans
  params           [{n, k: pos|kw|var|varkw, d?: default, t?: type}]   (+ moreParams when the list was cut)
  returns          {v: [what is returned], none: the end can be reached so nothing is returned, gen?, t?: type}
  raises           errors raised directly ("re-raise" and "a caught error" are special)
  changes          what it modifies: "self.x", "argument y", "global Z"
  outputs          "prints", "logs", "reads input"
  touches          [{kind, via}] outside systems it uses itself (see libkinds.KINDS)
  indirect         [{kind, via}] outside systems it only reaches through other functions; via is the first function
"""

from __future__ import annotations

import ast
import json
from collections import deque

from .libkinds import kind_of
from .model import FileInfo

MAX_PARAMS = 12
MAX_RAISES = 8
MAX_CHANGES = 6
MAX_RETURNS = 4
MAX_TOUCH = 6
MAX_VIA = 2
DEFAULT_CHARS = 30
INDIRECT_DEPTH = 8
CONTRACT_BUDGET_BYTES = 2_000_000          # all contracts together; the most complex functions are kept first

MUTATORS = {"append", "extend", "add", "update", "pop", "remove", "clear", "insert", "sort", "reverse", "setdefault",
            "discard", "popitem", "appendleft", "extendleft"}
_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
_LOGGERS = {"logging", "log", "logger", "_log", "_logger"}
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
# Calls that mean something outside the project even though the module is too common to list as a system.
CALL_KINDS = {"os.system": "processes", "os.popen": "processes", "os.remove": "files", "os.unlink": "files",
              "os.rename": "files", "os.makedirs": "files", "os.mkdir": "files", "os.rmdir": "files",
              "os.listdir": "files", "os.walk": "files"}
BUILTIN_PY = {"open": "files"}
BUILTIN_JS = {"fetch": "web-client", "XMLHttpRequest": "web-client"}


def short(text: str, limit: int = DEFAULT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "…"


# ----------------------------------------------------------------- Python
def _own_nodes(fn: ast.AST) -> list[ast.AST]:
    """Every node of the function in source order, not looking inside nested functions, lambdas or classes."""
    out, stack = [], list(reversed(fn.body))
    while stack:
        n = stack.pop()
        out.append(n)
        if isinstance(n, _NESTED):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(n))))
    return sorted((n for n in out if hasattr(n, "lineno")), key=lambda n: (n.lineno, n.col_offset))


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _root(node: ast.AST) -> tuple[str | None, str | None]:
    """(name at the bottom of an attribute/subscript chain, the first attribute taken from it)."""
    first = None
    while True:
        if isinstance(node, ast.Attribute):
            first = node.attr
            node = node.value
        elif isinstance(node, ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            break
    return (node.id if isinstance(node, ast.Name) else None), first


def _param(a: ast.arg, kind: str, default) -> dict:
    p = {"n": a.arg, "k": kind}
    if default is not None:
        p["d"] = short(_unparse(default))
    if a.annotation is not None:
        p["t"] = short(_unparse(a.annotation), 40)
    return p


def _kinds(e: ast.AST | None) -> list[str]:
    if e is None:
        return ["none"]
    if isinstance(e, ast.Constant):
        v = e.value
        if v is None:
            return ["none"]
        if isinstance(v, bool):
            return ["bool"]
        if isinstance(v, (int, float, complex)):
            return ["number"]
        return ["text"] if isinstance(v, (str, bytes)) else ["other"]
    if isinstance(e, ast.JoinedStr):
        return ["text"]
    if isinstance(e, (ast.List, ast.ListComp)):
        return ["list"]
    if isinstance(e, (ast.Dict, ast.DictComp)):
        return ["dict"]
    if isinstance(e, (ast.Set, ast.SetComp)):
        return ["set"]
    if isinstance(e, ast.Tuple):
        return ["tuple"]
    if isinstance(e, ast.GeneratorExp):
        return ["generator"]
    if isinstance(e, ast.Call):
        name = _dotted(e.func)
        return [f"call:{name}"] if name else ["computed"]
    if isinstance(e, ast.Name):
        return [f"name:{e.id}"]
    if isinstance(e, ast.Attribute):
        name = _dotted(e)
        return [f"attr:{name}"] if name else ["computed"]
    if isinstance(e, (ast.Compare, ast.BoolOp)) or (isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not)):
        return ["bool"]
    if isinstance(e, ast.IfExp):
        return _kinds(e.body) + _kinds(e.orelse)
    if isinstance(e, ast.Await):
        return _kinds(e.value)
    if isinstance(e, ast.Lambda):
        return ["function"]
    if isinstance(e, (ast.BinOp, ast.UnaryOp, ast.Subscript)):
        return ["computed"]
    return ["other"]


def _has_break(stmts: list[ast.stmt]) -> bool:
    for s in stmts:
        if isinstance(s, ast.Break):
            return True
        if isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
            if _has_break(s.orelse):          # a break in the loop's own body belongs to that inner loop
                return True
            continue
        for field in ("body", "orelse", "finalbody"):
            if _has_break(getattr(s, field, []) or []):
                return True
        for h in getattr(s, "handlers", []) or []:
            if _has_break(h.body):
                return True
    return False


def _always_exits(s: ast.stmt) -> bool:
    if isinstance(s, (ast.Return, ast.Raise)):
        return True
    if isinstance(s, ast.If):
        return bool(s.orelse) and _exits(s.body) and _exits(s.orelse)
    if isinstance(s, (ast.With, ast.AsyncWith)):
        return _exits(s.body)
    if isinstance(s, ast.Try):
        if s.finalbody and _exits(s.finalbody):
            return True
        return _exits(s.body) and all(_exits(h.body) for h in s.handlers)
    if isinstance(s, ast.While):
        return isinstance(s.test, ast.Constant) and bool(s.test.value) and not _has_break(s.body)
    return False


def _exits(stmts: list[ast.stmt]) -> bool:
    return any(_always_exits(s) for s in stmts)


def _error_name(exc: ast.AST) -> str:
    target = exc.func if isinstance(exc, ast.Call) else exc
    name = _dotted(target)
    if name and name.split(".")[-1][:1].isupper():
        return name
    return "a caught error"


def _targets(t: ast.AST):
    if isinstance(t, (ast.Tuple, ast.List)):
        for x in t.elts:
            yield from _targets(x)
    elif isinstance(t, ast.Starred):
        yield from _targets(t.value)
    else:
        yield t


def python_facts(fn: ast.AST, is_method: bool) -> dict:
    a = fn.args
    pos = list(a.posonlyargs) + list(a.args)
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    params = [_param(x, "pos", d) for x, d in zip(pos, defaults)]
    if a.vararg:
        params.append(_param(a.vararg, "var", None))
    params += [_param(x, "kw", d) for x, d in zip(a.kwonlyargs, a.kw_defaults)]
    if a.kwarg:
        params.append(_param(a.kwarg, "varkw", None))
    if is_method and params and params[0]["n"] in ("self", "cls"):
        params = params[1:]
    names = {p["n"] for p in params}
    own = _own_nodes(fn)

    values: list[str] = []
    raises: list[str] = []
    changes: list[str] = []
    outputs: list[str] = []
    gen = False

    def add(lst: list, item: str) -> None:
        if item not in lst:
            lst.append(item)

    def changed_by_write(target: ast.AST) -> None:
        base, first = _root(target)
        if base == "self" and first:
            add(changes, f"self.{first}")
        elif base in names and isinstance(target, (ast.Attribute, ast.Subscript)):
            add(changes, f"argument {base}")

    for n in own:
        if isinstance(n, ast.Return):
            for k in _kinds(n.value):
                add(values, k)
        elif isinstance(n, (ast.Yield, ast.YieldFrom)):
            gen = True
        elif isinstance(n, ast.Raise):
            add(raises, "re-raise" if n.exc is None else _error_name(n.exc))
        elif isinstance(n, ast.Assert):
            add(raises, "AssertionError")
        elif isinstance(n, ast.Global):
            for g in n.names:
                add(changes, f"global {g}")
        elif isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                for leaf in _targets(t):
                    changed_by_write(leaf)
        elif isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                if f.id == "print":
                    add(outputs, "prints")
                elif f.id == "input":
                    add(outputs, "reads input")
            elif isinstance(f, ast.Attribute):
                recv = _dotted(f.value)
                if (recv in _LOGGERS and f.attr in _LOG_METHODS):
                    add(outputs, "logs")
                elif recv in ("sys.stdout", "sys.stderr") and f.attr == "write":
                    add(outputs, "prints")
                if f.attr in MUTATORS:
                    base, first = _root(f.value)
                    if base == "self" and first:
                        add(changes, f"self.{first}")
                    elif base in names:
                        add(changes, f"argument {base}")

    returns: dict = {"v": values[:MAX_RETURNS], "none": not _exits(fn.body)}
    if len(values) > MAX_RETURNS:
        returns["moreValues"] = len(values) - MAX_RETURNS
    if gen:
        returns["gen"] = True
    if fn.returns is not None:
        returns["t"] = short(_unparse(fn.returns), 40)
    c = {"method": bool(is_method), "async": isinstance(fn, ast.AsyncFunctionDef), "params": params[:MAX_PARAMS],
         "returns": returns, "raises": raises[:MAX_RAISES], "changes": changes[:MAX_CHANGES], "outputs": outputs,
         "touches": [], "indirect": []}
    if len(params) > MAX_PARAMS:
        c["moreParams"] = len(params) - MAX_PARAMS
    return c


# ----------------------------------------------------------------- what it touches (needs the call graph)
def _local_names(files: dict[str, FileInfo]) -> set[str]:
    names: set[str] = set()
    for p in files:
        names.update(p.split("/")[:-1])
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        names.add(stem)
    return names


def _kind_of_call(chain: list[str], info: FileInfo, defined: set[str], local: set[str]) -> tuple[str, str] | None:
    head = chain[0]
    via = ".".join(chain)
    js = info.lang in ("JavaScript", "TypeScript")
    b = info.bindings.get(head)
    if b is None:
        if head in defined:
            return None
        if js:
            kind = BUILTIN_JS.get(head)
        else:
            kind = BUILTIN_PY.get(head) if len(chain) == 1 else None
        return (kind, via) if kind else None
    if js:
        spec = str(b[0])
        if spec.startswith((".", "/")):
            return None
        spec = spec[5:] if spec.startswith("node:") else spec
        pkg = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
        found = kind_of(pkg)
        return (found[0], via) if found else None
    level, module, attr = b
    if level or not module or module.split(".")[0] in local:
        return None
    dotted = ".".join([module, *( [attr] if attr else [] ), *chain[1:]])
    kind = CALL_KINDS.get(dotted) or CALL_KINDS.get(".".join([module, *chain[1:2]]))
    if kind:
        return kind, via
    found = kind_of(dotted)
    return (found[0], via) if found else None


def finish(files: dict[str, FileInfo]) -> None:
    """Add what each function touches: directly through its own calls, and indirectly through the functions it calls."""
    local = _local_names(files)
    direct: dict[str, dict[str, list[str]]] = {}
    sym_of: dict[str, tuple[str, object]] = {}
    for path, info in files.items():
        defined = {s.name for s in info.symbols if s.parent is None and s.kind in ("function", "class")}
        for s in info.symbols:
            sid = f"{path}::{s.qual}"
            sym_of[sid] = (path, s)
            kinds: dict[str, list[str]] = {}
            for chain, _line in s.raw_calls:
                hit = _kind_of_call(chain, info, defined, local)
                if hit and hit[1] not in kinds.setdefault(hit[0], []):
                    kinds[hit[0]].append(hit[1])
            direct[sid] = kinds
            if s.contract is not None:
                s.contract["touches"] = [{"kind": k, "via": v[:MAX_VIA]} for k, v in kinds.items()][:MAX_TOUCH]
    for sid, (path, s) in sym_of.items():
        if s.contract is None:
            continue
        seen, found = {sid}, {}
        queue = deque((c["id"], c["id"]) for c in s.callees if c["conf"] == "certain")
        depth = {c["id"]: 1 for c in s.callees if c["conf"] == "certain"}
        while queue:
            cur, first = queue.popleft()
            if cur in seen or cur not in sym_of:
                continue
            seen.add(cur)
            for kind in direct.get(cur, {}):
                found.setdefault(kind, first)
            if depth[cur] >= INDIRECT_DEPTH:
                continue
            for c in sym_of[cur][1].callees:
                if c["conf"] == "certain" and c["id"] not in seen:
                    depth.setdefault(c["id"], depth[cur] + 1)
                    queue.append((c["id"], first))
        own = set(direct[sid])
        s.contract["indirect"] = [{"kind": k, "via": sym_of[first][1].qual} for k, first in found.items() if k not in own][:MAX_TOUCH]


def apply_budget(files: dict[str, FileInfo]) -> int:
    """Drop the contracts of the least complex functions once all together would be too big. Returns how many."""
    items = []
    for path, info in files.items():
        for sym in info.symbols:
            if sym.contract is not None:
                items.append((-sym.complexity, path, sym.line, len(json.dumps(sym.contract, separators=(",", ":"))), sym))
    items.sort(key=lambda t: t[:3])
    used = trimmed = 0
    for _c, _p, _l, size, sym in items:
        if used + size > CONTRACT_BUDGET_BYTES:
            sym.contract = None
            trimmed += 1
        else:
            used += size
    return trimmed
