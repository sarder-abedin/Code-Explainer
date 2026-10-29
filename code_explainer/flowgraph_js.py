"""The step-by-step graph of a JavaScript / TypeScript function, from the tree-sitter syntax tree.

Same graphs and same plain-English rules as `flowgraph.py` for Python. Known simplification: a `switch` case that
runs on into the next one (no `break`) is drawn as leaving the switch.
"""

from __future__ import annotations

from .flowgraph import CODE_CHARS, _Graph, _join, _short, _tick

_FUNC_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_SIMPLE = {"expression_statement", "lexical_declaration", "variable_declaration", "function_declaration",
           "generator_function_declaration", "class_declaration", "abstract_class_declaration", "debugger_statement",
           "interface_declaration", "type_alias_declaration", "enum_declaration", "import_statement"}
_OPS = {">": "is more than", ">=": "is at least", "<": "is less than", "<=": "is at most",
        "===": "equals", "==": "equals", "!==": "is not", "!=": "is not"}
_INVERT = {">": "<=", ">=": "<", "<": ">=", "<=": ">", "===": "!==", "==": "!=", "!==": "===", "!=": "=="}
_MISSING = {"null", "undefined"}
_LITERALS = {"number", "string", "true", "false", "null", "undefined", "template_string", "regex"}


class JsGraph(_Graph):
    def __init__(self, data: bytes):
        super().__init__([])
        self.data = data

    # --- reading the tree
    def text(self, n) -> str:
        return self.data[n.start_byte:n.end_byte].decode("utf-8", "replace")

    def lines_of(self, n) -> tuple[int, int]:
        return n.start_point[0] + 1, n.end_point[0] + 1

    def unwrap(self, n):
        while n is not None and n.type == "parenthesized_expression" and n.named_child_count:
            n = n.named_children[0]
        return n

    def term(self, n) -> str:
        n = self.unwrap(n)
        if n.type in _LITERALS or (n.type == "identifier" and self.text(n) == "undefined"):
            return _short(self.text(n), 40)
        return _tick(self.text(n))

    # --- the words
    def clause(self, n, negate: bool = False) -> str:
        n = self.unwrap(n)
        t = n.type
        if t == "unary_expression" and self.text(n.child_by_field_name("operator")) == "!":
            return self.clause(n.child_by_field_name("argument"), not negate)
        if t == "binary_expression":
            op = self.text(n.child_by_field_name("operator"))
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            if op in ("&&", "||"):
                joined = (" and " if op == "&&" else " or ").join(self.clause(x) for x in (left, right))
                return f"it is not true that {joined}" if negate else joined
            if op == "instanceof":
                return f"{self.term(left)} is {'not ' if negate else ''}a {self.term(right)}"
            if op in _OPS:
                if negate:
                    op = _INVERT[op]
                for subject, other in ((left, right), (right, left)):
                    word = self.text(self.unwrap(other)) if self.unwrap(other).type in _MISSING | {"identifier"} else ""
                    if word in _MISSING and op in ("===", "==", "!==", "!="):
                        return f"{self.term(subject)} is missing ({word})" if op in ("===", "==") else f"{self.term(subject)} is set"
                return f"{self.term(left)} {_OPS[op]} {self.term(right)}"
        if t in ("identifier", "member_expression", "subscript_expression"):
            return f"{self.term(n)} is empty" if negate else f"{self.term(n)} has something in it"
        return f"{_tick(self.text(n))} is {'false' if negate else 'true'}"

    def callee(self, call) -> str:
        fn = call.child_by_field_name("function") or call.child_by_field_name("constructor")
        return _tick(self.text(fn)) if fn is not None else _tick(self.text(call))

    # --- hooks used by the shared statement loop
    def skipped(self, i: int, s) -> bool:
        if s.type in ("comment", "empty_statement"):
            return True
        return i == 0 and s.type == "expression_statement" and s.named_child_count == 1 and s.named_children[0].type == "string"

    def simple(self, s) -> bool:
        return s.type in _SIMPLE

    def span(self, s) -> tuple[int, int]:
        return self.lines_of(s)

    def src(self, s) -> str:
        return _short(self.text(s).rstrip(";"), CODE_CHARS)

    def phrase(self, s) -> tuple[str, str]:
        t = s.type
        if t in ("lexical_declaration", "variable_declaration"):
            names = ", ".join(_tick(self.text(d.child_by_field_name("name"))) for d in s.named_children
                              if d.type == "variable_declarator" and d.child_by_field_name("name") is not None)
            return (f"Set {names}", f"Remember a value called {names}") if names else ("Declare something", "Get something ready")
        if t == "expression_statement" and s.named_child_count:
            e = s.named_children[0]
            if e.type == "assignment_expression":
                x = _tick(self.text(e.child_by_field_name("left")))
                return f"Set {x}", f"Remember a value called {x}"
            if e.type == "augmented_assignment_expression":
                x = _tick(self.text(e.child_by_field_name("left")))
                return f"Update {x}", f"Change {x}"
            if e.type == "update_expression":
                x = _tick(self.text(e.child_by_field_name("argument")))
                return f"Update {x}", f"Change {x}"
            if e.type == "await_expression":
                call = next((c for c in e.named_children if c.type == "call_expression"), None)
                x = self.callee(call) if call is not None else _tick(self.text(e))
                return f"Wait for {x}", f"Wait for {x} to finish"
            if e.type == "call_expression":
                x = self.callee(e)
                return f"Call {x}", f"Ask {x} to do its job"
        if t in ("function_declaration", "generator_function_declaration", "class_declaration", "abstract_class_declaration"):
            name = s.child_by_field_name("name")
            x = _tick(self.text(name)) if name is not None else "something"
            return f"Define {x}", f"Teach the program a new helper: {x}"
        x = _tick(self.text(s).rstrip(";"))
        return f"Run {x}", f"Do {x}"

    def compound(self, s, entries: list) -> list:
        t = s.type
        if t == "if_statement":
            return self._if(s, entries, False)
        if t == "for_in_statement":
            return self._for_in(s, entries)
        if t in ("for_statement", "while_statement"):
            return self._for_or_while(s, entries)
        if t == "do_statement":
            return self._do(s, entries)
        if t == "try_statement":
            return self._try(s, entries)
        if t == "switch_statement":
            return self._switch(s, entries)
        if t == "return_statement":
            return self._return(s, entries)
        if t == "throw_statement":
            return self._throw(s, entries)
        if t in ("break_statement", "continue_statement"):
            return self.jump(t == "break_statement", s.start_point[0] + 1, entries)
        if t == "statement_block":
            return self.stmts(s.named_children, entries)
        if t == "labeled_statement":
            inner = s.child_by_field_name("body") or (s.named_children[-1] if s.named_children else None)
            return self.stmts([inner], entries) if inner is not None else entries
        node = self.add("step", self.text(s), f"Run {_tick(self.text(s))}", None, *self.lines_of(s))
        self.link(entries, node)
        return [(node, "")]

    def block(self, n, entries: list) -> list:
        return self.stmts(n.named_children if n.type == "statement_block" else [n], entries)

    # --- constructs
    def _if(self, s, entries: list, is_elif: bool) -> list:
        cond = self.unwrap(s.child_by_field_name("condition"))
        say = self.clause(cond) + "?"
        d = self.add("decision", f"{'else if' if is_elif else 'if'} ({self.text(cond)})", say, None,
                     s.start_point[0] + 1, cond.end_point[0] + 1)
        self.link(entries, d)
        yes = self.block(s.child_by_field_name("consequence"), [(d, "yes")])
        alt = s.child_by_field_name("alternative")
        if alt is None or not alt.named_child_count:
            return yes + [(d, "no")]
        inner = alt.named_children[0]
        no = self._if(inner, [(d, "no")], True) if inner.type == "if_statement" else self.block(inner, [(d, "no")])
        return yes + no

    def _header(self, s) -> str:
        body = s.child_by_field_name("body")
        return _short(self.data[s.start_byte:body.start_byte].decode("utf-8", "replace"), CODE_CHARS)

    def _loop(self, s, head_code: str, plain: str, kid: str | None, ways: tuple[str, str], entries: list) -> list:
        head = self.add("loop", head_code, plain, kid, s.start_point[0] + 1)
        self.link(entries, head)
        ctx = {"head": head, "breaks": [], "continues": [], "loop": True}
        self.loops.append(ctx)
        back = self.block(s.child_by_field_name("body"), [(head, ways[0])])
        self.loops.pop()
        self.link([(src, "" if src != head else label) for src, label in back], head)
        return [(head, ways[1])] + ctx["breaks"]

    def _for_in(self, s, entries: list) -> list:
        left, right = s.child_by_field_name("left"), s.child_by_field_name("right")
        a, b = _tick(self.text(left)), _tick(self.text(right))
        return self._loop(s, self._header(s), f"For each {a} in {b}", f"Take each {a} from {b}, one by one", ("each item", "done"), entries)

    def _for_or_while(self, s, entries: list) -> list:
        cond = s.child_by_field_name("condition")
        cond = self.unwrap(cond) if cond is not None and cond.type not in (";", "empty_statement") else None
        c = self.clause(cond) if cond is not None else "it never stops"
        return self._loop(s, self._header(s), f"Repeat while {c}", f"Keep going while {c}", ("yes", "no"), entries)

    def _do(self, s, entries: list) -> list:
        ctx = {"head": None, "breaks": [], "continues": [], "loop": True}
        self.loops.append(ctx)
        first = len(self.nodes)
        back = self.block(s.child_by_field_name("body"), entries)
        self.loops.pop()
        cond = self.unwrap(s.child_by_field_name("condition"))
        c = self.clause(cond)
        node = self.add("loop", f"do … while ({self.text(cond)})", f"Repeat while {c}", f"Keep going while {c}", s.end_point[0] + 1)
        self.link(back + ctx["continues"], node)
        if first < node:
            self.link([(node, "yes")], first)
        return [(node, "no")] + ctx["breaks"]

    def _try(self, s, entries: list) -> list:
        t = self.add("try", "try", "Try this", "Try this (with a plan if it goes wrong)", s.start_point[0] + 1)
        self.link(entries, t)
        exits = self.block(s.child_by_field_name("body"), [(t, "")])
        handler = s.child_by_field_name("handler")
        if handler is not None:
            param = handler.child_by_field_name("parameter")
            who = _tick(self.text(param)) if param is not None else ""
            h = self.add("handler", f"catch ({self.text(param)})" if param is not None else "catch",
                         f"If it fails ({who})" if who else "If it fails",
                         f"If it goes wrong ({who})" if who else "If it goes wrong", handler.start_point[0] + 1)
            self.link([(t, "on error")], h)
            exits = exits + self.block(handler.child_by_field_name("body"), [(h, "")])
        fin = s.child_by_field_name("finalizer")
        if fin is not None:
            body = fin.child_by_field_name("body")
            if exits:
                exits = self.block(body, exits)
            else:
                self.block(body, [(t, "always at the end")])        # every other path leaves; the block still runs, last
        return exits

    def _switch(self, s, entries: list) -> list:
        value = self.unwrap(s.child_by_field_name("value"))
        who = _tick(self.text(value))
        m = self.add("branch", f"switch ({self.text(value)})", f"Look at {who}", f"Look at {who} and pick a path", s.start_point[0] + 1)
        self.link(entries, m)
        ctx = {"head": None, "breaks": [], "continues": [], "loop": False}
        self.loops.append(ctx)
        exits, labels, has_default = [], [], False
        body = s.child_by_field_name("body")
        for case in (body.named_children if body is not None else []):
            if case.type == "switch_case":
                labels.append(_short(self.text(case.child_by_field_name("value")), 40))
            elif case.type == "switch_default":
                labels.append("default")
                has_default = True
            else:
                continue
            stmts = case.children_by_field_name("body")
            if stmts:
                exits += self.stmts(stmts, [(m, " / ".join(labels))])
                labels = []
        self.loops.pop()
        exits += ctx["breaks"]
        return exits if has_default else exits + [(m, "no match")]

    def _return(self, s, entries: list) -> list:
        value = s.named_children[0] if s.named_child_count else None
        if value is None:
            node = self.add("return", "return", "Finish", "Finish up", *self.lines_of(s))
        else:
            v = _tick(self.text(value))
            node = self.add("return", f"return {self.text(value)}", f"Return {v}", f"Hand back {v}", *self.lines_of(s))
        self.link(entries, node)
        return []

    def _throw(self, s, entries: list) -> list:
        value = s.named_children[0] if s.named_child_count else None
        e = _tick(self.text(value)) if value is not None else "an error"
        node = self.add("raise", f"throw {self.text(value)}" if value is not None else "throw", f"Stop with an error: {e}",
                        f"Shout that something went wrong: {e}", *self.lines_of(s))
        self.link(entries, node)
        return []


def build_js(data: bytes, fn) -> dict | None:
    """The graph of a function (or of its body), or None when it has no choices to show."""
    body = fn
    if body.type in _FUNC_VALUES:
        body = body.child_by_field_name("body")
    if body is None or body.type != "statement_block":
        return None
    g = JsGraph(data)
    start = g.add("start", "function", "Start", None, fn.start_point[0] + 1)
    exits = g.stmts(body.named_children, [(start, "")])
    if exits and not g.truncated:
        end = g.add("end", "", "Finish", "Finish up", fn.end_point[0] + 1)
        g.link(exits, end)
    if not any(n["kind"] in ("decision", "loop", "try", "branch") for n in g.nodes):
        return None
    return {"nodes": g.nodes, "edges": g.edges, "truncated": g.truncated}
