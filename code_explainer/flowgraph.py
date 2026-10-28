"""The step-by-step graph of one function: its branches, loops, error paths and early exits.

A graph is plain JSON: nodes `{id, kind, code, say: {kid, plain}, line, end}` and edges `{from, to, label}`.
Node 0 is always the start. The words are worked out from the syntax with fixed rules (nothing is guessed by a
model), and the exact code is kept beside them for the technical level.

Kinds: start, end, step, decision, loop, try, handler, branch (match), return, raise, more.
"""

from __future__ import annotations

import ast
import json
import re

from . import flowlayout

MAX_NODES = 80
FLOW_BUDGET_BYTES = 3_000_000          # all charts together; the most complex functions are kept first
SIZES = {"start": (120, 36), "end": (120, 36), "step": (230, 56), "decision": (230, 84), "loop": (230, 84),
         "branch": (230, 70), "try": (170, 44), "handler": (230, 52), "return": (200, 50), "raise": (200, 50),
         "more": (200, 40)}
CODE_CHARS = 90
TERM_CHARS = 40
_SPACE = re.compile(r"\s+")
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_TRY = tuple(t for t in (ast.Try, getattr(ast, "TryStar", None)) if t)
_SKIPPED = (ast.Global, ast.Nonlocal)
_SIMPLE = (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Import, ast.ImportFrom, ast.Delete, ast.Pass,
           ast.Assert, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_OPS = {ast.Gt: "is more than", ast.GtE: "is at least", ast.Lt: "is less than", ast.LtE: "is at most",
        ast.Eq: "equals", ast.NotEq: "is not", ast.In: "is in", ast.NotIn: "is not in", ast.Is: "is", ast.IsNot: "is not"}
_INVERT = {ast.Gt: ast.LtE, ast.GtE: ast.Lt, ast.Lt: ast.GtE, ast.LtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
           ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}


def _short(text: str, limit: int = CODE_CHARS) -> str:
    text = "".join(c if c.isprintable() else " " for c in _SPACE.sub(" ", text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _code(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # an odd node must never stop the chart
        return "…"


def _tick(text: str) -> str:
    return f"`{_short(text, TERM_CHARS)}`"


# ----------------------------------------------------------------- the words
def _term(e: ast.AST) -> str:
    if isinstance(e, ast.Constant):
        return _short(_code(e), TERM_CHARS)
    return _tick(_code(e))


def _clause(e: ast.AST, negate: bool = False) -> str:
    """A condition as a statement about the world: "`x` is more than 0"."""
    if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not):
        return _clause(e.operand, not negate)
    if isinstance(e, ast.Compare) and len(e.ops) == 1:
        op, right = type(e.ops[0]), e.comparators[0]
        if negate:
            op = _INVERT[op]
        left = _term(e.left)
        if isinstance(right, ast.Constant) and right.value is None and op in (ast.Is, ast.IsNot):
            return f"{left} is missing (None)" if op is ast.Is else f"{left} is set"
        return f"{left} {_OPS[op]} {_term(right)}"
    if isinstance(e, ast.BoolOp):
        joined = (" and " if isinstance(e.op, ast.And) else " or ").join(_clause(v) for v in e.values)
        return f"it is not true that {joined}" if negate else joined
    if isinstance(e, (ast.Name, ast.Attribute, ast.Subscript)):
        return f"{_term(e)} is empty" if negate else f"{_term(e)} has something in it"
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id == "isinstance" and len(e.args) == 2:
        return f"{_term(e.args[0])} is {'not ' if negate else ''}a {_term(e.args[1])}"
    return f"{_tick(_code(e))} is {'false' if negate else 'true'}"


def _callee(call: ast.Call) -> str:
    return _tick(_code(call.func))


def _lower(text: str) -> str:
    return text[:1].lower() + text[1:]


def _phrase(s: ast.stmt) -> tuple[str, str]:
    """(plain, beginner) wording for one simple statement."""
    if isinstance(s, ast.Assign):
        t = _tick(", ".join(_code(x) for x in s.targets))
        return f"Set {t}", f"Remember a value called {t}"
    if isinstance(s, ast.AnnAssign):
        t = _tick(_code(s.target))
        return (f"Set {t}", f"Remember a value called {t}") if s.value is not None else (f"Declare {t}", f"Get {t} ready")
    if isinstance(s, ast.AugAssign):
        t = _tick(_code(s.target))
        return f"Update {t}", f"Change {t}"
    if isinstance(s, ast.Expr):
        v = s.value
        if isinstance(v, ast.Await) and isinstance(v.value, ast.Call):
            c = _callee(v.value)
            return f"Wait for {c}", f"Wait for {c} to finish"
        if isinstance(v, ast.Call):
            c = _callee(v)
            return f"Call {c}", f"Ask {c} to do its job"
        c = _tick(_code(v))
        return f"Run {c}", f"Do {c}"
    if isinstance(s, (ast.Import, ast.ImportFrom)):
        mod = _tick(getattr(s, "module", None) or ", ".join(a.name for a in s.names))
        return f"Load {mod}", f"Fetch {mod}"
    if isinstance(s, ast.Delete):
        t = _tick(", ".join(_code(x) for x in s.targets))
        return f"Remove {t}", f"Throw away {t}"
    if isinstance(s, ast.Pass):
        return "Do nothing", "Do nothing"
    if isinstance(s, ast.Assert):
        c = _clause(s.test)
        return f"Check that {c}", f"Make sure that {c}"
    if isinstance(s, (*_FUNCS, ast.ClassDef)):
        t = _tick(s.name)
        return f"Define {t}", f"Teach the program a new helper: {t}"
    c = _tick(_code(s))
    return f"Run {c}", f"Do {c}"


def _join(phrases: list[str]) -> str:
    shown = phrases[:3]
    text = ", then ".join([shown[0]] + [_lower(p) for p in shown[1:]])
    rest = len(phrases) - len(shown)
    return text + (f", then {rest} more step{'s' if rest > 1 else ''}" if rest else "")


# ----------------------------------------------------------------- the graph
class _Graph:
    def __init__(self, lines: list[str]):
        self.lines = lines
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.truncated = False
        self.loops: list[dict] = []

    def add(self, kind: str, code: str, plain: str, kid: str | None, line: int, end: int | None = None) -> int:
        self.nodes.append({"id": len(self.nodes), "kind": kind, "code": _short(code),
                           "say": {"kid": _short(kid if kid is not None else plain, 160), "plain": _short(plain, 160)},
                           "line": line, "end": max(end or line, line)})
        return len(self.nodes) - 1

    def link(self, entries: list[tuple[int, str]], to: int) -> None:
        for src, label in entries:
            self.edges.append({"from": src, "to": to, "label": label})

    def stop_here(self, entries: list[tuple[int, str]], line: int) -> list:
        if not self.truncated:
            self.truncated = True
            more = self.add("more", "", "…and more steps that are not shown", "…and more steps that are not shown", line)
            self.link(entries, more)
        return []

    # --- statements (the language-specific parts are the small hooks below)
    def skipped(self, i: int, s) -> bool:
        return isinstance(s, _SKIPPED) or (i == 0 and isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                           and isinstance(s.value.value, str))      # globals and the docstring

    def simple(self, s) -> bool:
        return isinstance(s, _SIMPLE)

    def phrase(self, s) -> tuple[str, str]:
        return _phrase(s)

    def span(self, s) -> tuple[int, int]:
        return s.lineno, s.end_lineno or s.lineno

    def src(self, s) -> str:
        return _short(_code(s), CODE_CHARS)

    def stmts(self, body: list, entries: list[tuple[int, str]]) -> list[tuple[int, str]]:
        pending: list = []

        def flush() -> None:
            nonlocal entries
            if not pending:
                return
            phrases = [self.phrase(s) for s in pending]
            node = self.add("step", "; ".join(self.src(s) for s in pending), _join([p for p, _ in phrases]),
                            _join([k for _, k in phrases]), self.span(pending[0])[0], max(self.span(s)[1] for s in pending))
            self.link(entries, node)
            entries = [(node, "")]
            pending.clear()

        for i, s in enumerate(body):
            if self.skipped(i, s):
                continue
            if self.simple(s):
                pending.append(s)
                continue
            flush()
            if self.truncated or len(self.nodes) >= MAX_NODES:
                return self.stop_here(entries, self.span(s)[0])
            entries = self.compound(s, entries)
            if not entries:
                return []
        flush()
        return entries

    def compound(self, s: ast.stmt, entries: list) -> list:
        if isinstance(s, ast.If):
            return self._if(s, entries)
        if isinstance(s, (ast.For, ast.AsyncFor)):
            return self._for(s, entries)
        if isinstance(s, ast.While):
            return self._while(s, entries)
        if isinstance(s, _TRY):
            return self._try(s, entries)
        if isinstance(s, (ast.With, ast.AsyncWith)):
            return self._with(s, entries)
        if isinstance(s, ast.Return):
            return self._return(s, entries)
        if isinstance(s, ast.Raise):
            return self._raise(s, entries)
        if isinstance(s, (ast.Break, ast.Continue)):
            return self._jump(s, entries)
        if hasattr(ast, "Match") and isinstance(s, ast.Match):
            return self._match(s, entries)
        node = self.add("step", _code(s), f"Run {_tick(_code(s))}", None, s.lineno, s.end_lineno)
        self.link(entries, node)
        return [(node, "")]

    def _if(self, s: ast.If, entries: list) -> list:
        elif_ = 0 < s.lineno <= len(self.lines) and self.lines[s.lineno - 1].lstrip().startswith("elif")
        say = _clause(s.test) + "?"
        d = self.add("decision", f"{'elif' if elif_ else 'if'} {_code(s.test)}:", say, None, s.lineno, s.test.end_lineno)
        self.link(entries, d)
        yes = self.stmts(s.body, [(d, "yes")])
        no = self.stmts(s.orelse, [(d, "no")]) if s.orelse else [(d, "no")]
        return yes + no

    def _for(self, s, entries: list) -> list:
        t, it = _tick(_code(s.target)), _tick(_code(s.iter))
        head = self.add("loop", f"{'async ' if isinstance(s, ast.AsyncFor) else ''}for {_code(s.target)} in {_code(s.iter)}:",
                        f"For each {t} in {it}", f"Take each {t} from {it}, one by one", s.lineno)
        self.link(entries, head)
        ctx = {"head": head, "breaks": [], "continues": [], "loop": True}
        self.loops.append(ctx)
        back = self.stmts(s.body, [(head, "each item")])
        self.loops.pop()
        self.link([(src, "" if src != head else label) for src, label in back], head)
        done = [(head, "done")]
        if s.orelse:
            done = self.stmts(s.orelse, done)
        return done + ctx["breaks"]

    def _while(self, s: ast.While, entries: list) -> list:
        c = _clause(s.test)
        head = self.add("loop", f"while {_code(s.test)}:", f"Keep going while {c}", None, s.lineno, s.test.end_lineno)
        self.link(entries, head)
        ctx = {"head": head, "breaks": [], "continues": [], "loop": True}
        self.loops.append(ctx)
        back = self.stmts(s.body, [(head, "yes")])
        self.loops.pop()
        self.link(back, head)
        done = [(head, "no")]
        if s.orelse:
            done = self.stmts(s.orelse, done)
        return done + ctx["breaks"]

    def _try(self, s, entries: list) -> list:
        t = self.add("try", "try:", "Try this", "Try this (with a plan if it goes wrong)", s.lineno)
        self.link(entries, t)
        exits = self.stmts(s.body, [(t, "")])
        if s.orelse:
            exits = self.stmts(s.orelse, exits)
        for h in s.handlers:
            kind = _code(h.type) if h.type is not None else ""
            code = "except" + (f" {kind}" if kind else "") + (f" as {h.name}" if h.name else "")
            plain = f"If it fails with {_tick(kind)}" if kind else "If anything fails"
            kid = f"If it goes wrong ({_tick(kind)})" if kind else "If anything goes wrong"
            hid = self.add("handler", code, plain, kid, h.lineno)
            self.link([(t, "on error")], hid)
            exits = exits + self.stmts(h.body, [(hid, "")])
        if s.finalbody:
            exits = self._finally(s.finalbody, exits, t)
        return exits

    def _finally(self, body: list, exits: list, try_node: int) -> list:
        """The finally block runs on every path. When no path reaches it normally (they all return, raise or jump) it is
        still drawn, hanging off the try, and nothing continues after it."""
        if exits:
            return self.stmts(body, exits)
        self.stmts(body, [(try_node, "always at the end")])
        return []

    def _with(self, s, entries: list) -> list:
        items = ", ".join(_code(i.context_expr) for i in s.items)
        t = _tick(items)
        node = self.add("step", f"with {items}:", f"Open {t}", f"Open {t} and use it", s.lineno)
        self.link(entries, node)
        return self.stmts(s.body, [(node, "")])

    def _return(self, s: ast.Return, entries: list) -> list:
        if s.value is None:
            node = self.add("return", "return", "Finish", "Finish up", s.lineno, s.end_lineno)
        else:
            v = _tick(_code(s.value))
            node = self.add("return", f"return {_code(s.value)}", f"Return {v}", f"Hand back {v}", s.lineno, s.end_lineno)
        self.link(entries, node)
        return []

    def _raise(self, s: ast.Raise, entries: list) -> list:
        if s.exc is None:
            node = self.add("raise", "raise", "Pass the error on", "Pass the problem on", s.lineno)
        else:
            e = _tick(_code(s.exc))
            node = self.add("raise", f"raise {_code(s.exc)}", f"Stop with an error: {e}", f"Shout that something went wrong: {e}", s.lineno, s.end_lineno)
        self.link(entries, node)
        return []

    def _jump(self, s, entries: list) -> list:
        leave = isinstance(s, ast.Break)
        return self.jump(leave, s.lineno, entries)

    def jump(self, leave: bool, line: int, entries: list) -> list:
        """break (leave the nearest loop or switch) or continue (the nearest loop)."""
        node = self.add("step", "break" if leave else "continue", "Leave the loop" if leave else "Skip to the next item",
                        "Stop going round and leave the loop" if leave else "Skip ahead to the next one", line)
        self.link(entries, node)
        loops = [c for c in self.loops if c["loop"]]
        target = self.loops[-1] if leave and self.loops else (loops[-1] if loops else None)
        if target is None:
            return [(node, "")]
        if leave:
            target["breaks"].append((node, ""))
        elif target["head"] is not None:
            self.link([(node, "")], target["head"])
        else:
            target["continues"].append((node, ""))
        return []

    def _match(self, s, entries: list) -> list:
        subject = _tick(_code(s.subject))
        m = self.add("branch", f"match {_code(s.subject)}:", f"Look at {subject}", f"Look at {subject} and pick a path", s.lineno)
        self.link(entries, m)
        exits, wildcard = [], False
        for case in s.cases:
            label = _short(_code(case.pattern) + (f" if {_code(case.guard)}" if case.guard is not None else ""), 40)
            wildcard = wildcard or (case.guard is None and isinstance(case.pattern, getattr(ast, "MatchAs")) and case.pattern.pattern is None)
            exits += self.stmts(case.body, [(m, label)])
        return exits if wildcard else exits + [(m, "no match")]


def build_python(fn: ast.AST, lines: list[str]) -> dict | None:
    """The graph of a function, or None when it has no choices to show (a straight line needs no chart)."""
    g = _Graph(lines)
    start = g.add("start", f"def {fn.name}", "Start", None, fn.lineno)
    exits = g.stmts(fn.body, [(start, "")])
    if exits and not g.truncated:
        end = g.add("end", "", "Finish", "Finish up", fn.end_lineno or fn.lineno)
        g.link(exits, end)
    if not any(n["kind"] in ("decision", "loop", "try", "branch") for n in g.nodes):
        return None
    return {"nodes": g.nodes, "edges": g.edges, "truncated": g.truncated}


def finalize(graph: dict) -> dict:
    """Give every box a place and every arrow a path, ready to draw."""
    nodes = graph["nodes"]
    sizes = {n["id"]: SIZES.get(n["kind"], (230, 56)) for n in nodes}
    merged: dict[tuple[int, int], list[str]] = {}
    for e in graph["edges"]:
        labels = merged.setdefault((e["from"], e["to"]), [])
        if e["label"] and e["label"] not in labels:
            labels.append(e["label"])
    res = flowlayout.layout(sizes, list(merged), order=[n["id"] for n in nodes])
    placed = [{**n, "x": res["pos"][n["id"]][0], "y": res["pos"][n["id"]][1], "w": sizes[n["id"]][0], "h": sizes[n["id"]][1]} for n in nodes]
    edges = []
    for (a, b), labels in merged.items():
        path = [[x, y] for x, y in res["paths"][(a, b)]]
        (x0, y0), (x1, y1) = path[0], path[1]
        edges.append({"from": a, "to": b, "label": " / ".join(labels), "path": path, "back": (a, b) in res["back"],
                      "lx": round((x0 + x1) / 2), "ly": round((y0 + y1) / 2)})
    return {"nodes": placed, "edges": edges, "w": res["width"], "h": res["height"], "truncated": graph["truncated"]}


def apply_budget(files: dict) -> int:
    """Drop the charts of the least complex functions once all charts together would be too big. Returns how many."""
    items = []
    for path, info in files.items():
        for sym in info.symbols:
            if sym.flow:
                items.append((-sym.complexity, path, sym.line, len(json.dumps(sym.flow, separators=(",", ":"))), sym))
    items.sort(key=lambda t: t[:3])
    used = trimmed = 0
    for _c, _p, _l, size, sym in items:
        if used + size > FLOW_BUDGET_BYTES:
            sym.flow = None
            trimmed += 1
        else:
            used += size
    return trimmed
