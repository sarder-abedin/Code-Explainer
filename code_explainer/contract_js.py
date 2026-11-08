"""The contract of a JavaScript / TypeScript function, from the tree-sitter syntax tree (see contract.py)."""

from __future__ import annotations

from .contract import (MAX_CHANGES, MAX_PARAMS, MAX_RAISES, MAX_RETURNS, short)

_NESTED = {"function_declaration", "generator_function_declaration", "function_expression", "function", "generator_function",
           "arrow_function", "method_definition", "class_declaration", "class", "abstract_class_declaration"}
_LOOPS = {"for_statement", "for_in_statement", "while_statement", "do_statement", "switch_statement"}
_MUTATORS = {"push", "pop", "shift", "unshift", "splice", "sort", "reverse", "set", "add", "delete", "clear", "fill"}
_CONSOLE = {"console.log", "console.info", "console.warn", "console.error", "console.debug", "console.table",
            "process.stdout.write", "process.stderr.write"}
_BOOL_OPS = {">", "<", ">=", "<=", "==", "===", "!=", "!==", "&&", "||", "instanceof", "in"}


def _simple_chain(text, n) -> str | None:
    """`a.b.c` or `this.x` written with plain names only, else None."""
    if n.type in ("identifier", "this", "property_identifier"):
        return text(n)
    if n.type == "member_expression":
        obj, prop = n.child_by_field_name("object"), n.child_by_field_name("property")
        base = _simple_chain(text, obj) if obj is not None else None
        return f"{base}.{text(prop)}" if base and prop is not None else None
    return None


def _unwrap(n):
    while n is not None and n.type == "parenthesized_expression" and n.named_child_count:
        n = n.named_children[0]
    return n


def _kinds(text, n) -> list[str]:
    n = _unwrap(n)
    if n is None:
        return ["none"]
    t = n.type
    if t in ("number",):
        return ["number"]
    if t in ("string", "template_string"):
        return ["text"]
    if t in ("true", "false"):
        return ["bool"]
    if t in ("null", "undefined") or (t == "identifier" and text(n) == "undefined"):
        return ["none"]
    if t == "array":
        return ["list"]
    if t == "object":
        return ["dict"]
    if t in ("arrow_function", "function_expression", "function", "generator_function"):
        return ["function"]
    if t == "await_expression" and n.named_child_count:
        return _kinds(text, n.named_children[0])
    if t == "ternary_expression":
        return _kinds(text, n.child_by_field_name("consequence")) + _kinds(text, n.child_by_field_name("alternative"))
    if t == "call_expression":
        name = _simple_chain(text, n.child_by_field_name("function"))
        return [f"call:{name}"] if name else ["computed"]
    if t == "new_expression":
        ctor = n.child_by_field_name("constructor")
        name = _simple_chain(text, ctor) if ctor is not None else None
        return [f"call:{name}"] if name else ["computed"]
    if t == "identifier" or t == "this":
        return [f"name:{text(n)}"]
    if t == "member_expression":
        name = _simple_chain(text, n)
        return [f"attr:{name}"] if name else ["computed"]
    if t == "binary_expression":
        op = text(n.child_by_field_name("operator"))
        return ["bool"] if op in _BOOL_OPS else ["computed"]
    if t == "unary_expression":
        op = text(n.child_by_field_name("operator"))
        return ["bool"] if op == "!" else ["text"] if op == "typeof" else ["computed"]
    if t in ("subscript_expression", "update_expression", "assignment_expression", "augmented_assignment_expression"):
        return ["computed"]
    return ["other"]


def _own(node) -> list:
    """The function body's nodes in source order, not entering nested functions or classes."""
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        out.append(n)
        if n is not node and n.type in _NESTED:
            continue
        stack.extend(reversed(n.children))
    return out


def _has_break(n) -> bool:
    for c in n.children:
        if c.type == "break_statement":
            return True
        if c.type in _LOOPS:
            continue
        if _has_break(c):
            return True
    return False


def _always_exits(n) -> bool:
    t = n.type
    if t in ("return_statement", "throw_statement"):
        return True
    if t == "statement_block":
        return _exits(n.named_children)
    if t == "if_statement":
        alt = n.child_by_field_name("alternative")
        if alt is None or not alt.named_child_count:
            return False
        return _always_exits(n.child_by_field_name("consequence")) and _always_exits(alt.named_children[0])
    if t == "try_statement":
        fin = n.child_by_field_name("finalizer")
        if fin is not None and _always_exits(fin.child_by_field_name("body")):
            return True
        handler = n.child_by_field_name("handler")
        return _always_exits(n.child_by_field_name("body")) and (handler is None or _always_exits(handler.child_by_field_name("body")))
    if t == "while_statement":
        cond = _unwrap(n.child_by_field_name("condition"))
        return cond is not None and cond.type == "true" and not _has_break(n.child_by_field_name("body"))
    return False


def _exits(stmts) -> bool:
    return any(_always_exits(s) for s in stmts)


def _params(text, fn) -> tuple[list[dict], set[str]]:
    fp = fn.child_by_field_name("parameters")
    items = list(fp.named_children) if fp is not None else ([fn.child_by_field_name("parameter")] if fn.child_by_field_name("parameter") is not None else [])
    params, names = [], set()
    for it in items:
        if it is None or it.type == "comment":
            continue
        kind, default, ann, node = "pos", None, None, it
        if it.type in ("required_parameter", "optional_parameter"):
            node = it.child_by_field_name("pattern") or it
            default = it.child_by_field_name("value")
            ty = it.child_by_field_name("type")
            ann = text(ty).lstrip(":").strip() if ty is not None else None
        if node.type == "assignment_pattern":
            default = node.child_by_field_name("right")
            node = node.child_by_field_name("left")
        if node.type == "rest_pattern":
            kind = "var"
            node = node.named_children[0] if node.named_child_count else node
        if node.type == "this":
            continue
        p = {"n": text(node), "k": kind}
        if default is not None:
            p["d"] = short(text(default))
        if ann:
            p["t"] = short(ann, 40)
        params.append(p)
        if node.type == "identifier":
            names.add(text(node))
    return params, names


def _root(text, n):
    """(name at the bottom of a member chain, the first property taken from it)."""
    first = None
    while n is not None and n.type in ("member_expression", "subscript_expression"):
        prop = n.child_by_field_name("property") or n.child_by_field_name("index")
        first = text(prop) if prop is not None and n.type == "member_expression" else first
        n = n.child_by_field_name("object")
    return (text(n) if n is not None and n.type in ("identifier", "this") else None), first


def facts(text, fn, is_method: bool) -> dict:
    params, names = _params(text, fn)
    body = fn.child_by_field_name("body")
    scope = _own(body) if body is not None else []
    values: list[str] = []
    raises: list[str] = []
    changes: list[str] = []
    outputs: list[str] = []

    def add(lst: list, item: str) -> None:
        if item not in lst:
            lst.append(item)

    def written(target) -> None:
        base, first = _root(text, target)
        if target is None or target.type not in ("member_expression", "subscript_expression"):
            return
        if base == "this" and first:
            add(changes, f"this.{first}")
        elif base in names:
            add(changes, f"argument {base}")

    if body is not None and body.type != "statement_block":          # an arrow function with an expression body
        for k in _kinds(text, body):
            add(values, k)
    for n in scope:
        t = n.type
        if t == "return_statement":
            expr = next((c for c in n.named_children if c.type != "comment"), None)
            for k in _kinds(text, expr):
                add(values, k)
        elif t == "throw_statement":
            expr = _unwrap(n.named_children[0]) if n.named_child_count else None
            name = None
            if expr is not None and expr.type == "new_expression":
                ctor = expr.child_by_field_name("constructor")
                name = _simple_chain(text, ctor) if ctor is not None else None
            elif expr is not None and expr.type == "call_expression":
                name = _simple_chain(text, expr.child_by_field_name("function"))
            elif expr is not None:
                name = _simple_chain(text, expr)
            add(raises, name if name and name.split(".")[-1][:1].isupper() else "a caught error")
        elif t in ("assignment_expression", "augmented_assignment_expression"):
            written(n.child_by_field_name("left"))
        elif t == "update_expression":
            written(n.child_by_field_name("argument"))
        elif t == "call_expression":
            callee = n.child_by_field_name("function")
            name = _simple_chain(text, callee) if callee is not None else None
            if name in _CONSOLE:
                add(outputs, "prints")
            if callee is not None and callee.type == "member_expression":
                prop = callee.child_by_field_name("property")
                if prop is not None and text(prop) in _MUTATORS:
                    base, first = _root(text, callee.child_by_field_name("object"))
                    obj = callee.child_by_field_name("object")
                    if base == "this" and first:
                        add(changes, f"this.{first}")
                    elif base in names and (obj.type == "identifier" or obj.type == "member_expression"):
                        add(changes, f"argument {base}")

    exits = body is None or body.type != "statement_block" or _exits(body.named_children)
    returns: dict = {"v": values[:MAX_RETURNS], "none": not exits}
    if len(values) > MAX_RETURNS:
        returns["moreValues"] = len(values) - MAX_RETURNS
    if fn.type in ("generator_function_declaration", "generator_function") or any(c.type == "*" for c in fn.children):
        returns["gen"] = True
    ret = fn.child_by_field_name("return_type")
    if ret is not None:
        returns["t"] = short(text(ret).lstrip(":").strip(), 40)
    c = {"method": bool(is_method), "async": any(ch.type == "async" for ch in fn.children), "params": params[:MAX_PARAMS],
         "returns": returns, "raises": raises[:MAX_RAISES], "changes": changes[:MAX_CHANGES], "outputs": outputs,
         "touches": [], "indirect": []}
    if len(params) > MAX_PARAMS:
        c["moreParams"] = len(params) - MAX_PARAMS
    return c
