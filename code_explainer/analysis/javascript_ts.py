"""JavaScript / TypeScript analysis with tree-sitter (optional dependency).

Gives exact symbol ranges, class methods, call sites and import bindings. When
tree-sitter is not installed `AVAILABLE` is False and the regex scanner in
`javascript.py` is used instead.
"""

from __future__ import annotations

import re

from ..model import FileInfo, Symbol

try:
    import tree_sitter_javascript as _tsjs
    import tree_sitter_typescript as _tsts
    from tree_sitter import Language, Parser

    AVAILABLE = True
except Exception:  # pragma: no cover - exercised by the fallback CI job
    AVAILABLE = False

_PARSERS: dict = {}
_FUNC_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_BRANCHES = {"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
             "switch_case", "catch_clause", "ternary_expression"}
_LOGICAL = {"&&", "||", "??"}
_DIVIDER = re.compile(r"^[-=*#~_/\s]{3,}|[-=*#~_\s]{3,}$")
_ROUTE_DECO = re.compile(r"^@(Get|Post|Put|Delete|Patch|All|Head|Options)\b")
_HANDLER_DECO = re.compile(r"^@(Command|MessagePattern|EventPattern|Cron|OnEvent|Process|Subscribe\w*)\b")


def _parser_for(path: str):
    kind = "tsx" if path.endswith(".tsx") else "ts" if path.endswith((".ts", ".mts", ".cts")) else "js"
    if kind not in _PARSERS:
        lang = {"js": _tsjs.language, "ts": _tsts.language_typescript, "tsx": _tsts.language_tsx}[kind]()
        _PARSERS[kind] = Parser(Language(lang))
    return _PARSERS[kind]


class _Ctx:
    def __init__(self, info: FileInfo, data: bytes):
        self.info = info
        self.data = data

    def text(self, node) -> str:
        return self.data[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def string(self, node) -> str:
        return self.text(node).strip("'\"`")

    def doc_above(self, node) -> str:
        prev = node.prev_sibling
        if prev is None or prev.type != "comment" or prev.end_point[0] < node.start_point[0] - 1:
            return ""
        for raw in self.text(prev).splitlines():
            line = raw.strip().lstrip("/").lstrip("*").rstrip("/").rstrip("*").strip()
            if line and not line.startswith("@"):
                return "" if _DIVIDER.search(line) else line[:160]
        return ""

    def chain(self, node) -> list[str]:
        t = node.type
        if t == "identifier":
            return [self.text(node)]
        if t in ("this", "super"):
            return [t]
        if t == "member_expression":
            obj, prop = node.child_by_field_name("object"), node.child_by_field_name("property")
            if obj is None or prop is None:
                return ["?"]
            return self.chain(obj) + [self.text(prop)]
        if t in ("non_null_expression", "parenthesized_expression", "await_expression") and node.named_child_count:
            return self.chain(node.named_children[0])
        return ["?"]

    def complexity(self, node) -> int:
        score, stack = 1, [node]
        while stack:
            n = stack.pop()
            if n.type in _BRANCHES:
                score += 1
            elif n.type == "binary_expression":
                op = n.child_by_field_name("operator")
                if op is not None and self.text(op) in _LOGICAL:
                    score += 1
            stack.extend(n.children)
        return score

    def walk_calls(self, node, out: list) -> None:
        """Collect call sites under `node`; also picks up require()/import() edges."""
        found: list = []
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in ("call_expression", "new_expression"):
                fn = n.child_by_field_name("function" if n.type == "call_expression" else "constructor")
                if fn is not None and fn.type == "import":
                    self._import_arg(n)
                elif fn is not None and fn.type == "identifier" and self.text(fn) == "require" and n.type == "call_expression":
                    self._require(n)
                elif fn is not None:
                    chain = self.chain(fn)
                    if chain != ["?"]:
                        found.append((chain, n.start_point[0] + 1, n.start_point[1]))
            stack.extend(n.children)
        found.sort(key=lambda c: (c[1], c[2]))
        seen = {(tuple(c), l) for c, l in out}
        for chain, line, _col in found:
            if (tuple(chain), line) not in seen:
                seen.add((tuple(chain), line))
                out.append((chain, line))

    def _first_string_arg(self, call):
        args = call.child_by_field_name("arguments")
        if args is not None and args.named_child_count and args.named_children[0].type == "string":
            return self.string(args.named_children[0])
        return None

    def _import_arg(self, call) -> None:
        spec = self._first_string_arg(call)
        if spec and spec not in self.info.raw_imports:
            self.info.raw_imports.append(spec)

    def _require(self, call) -> None:
        spec = self._first_string_arg(call)
        if not spec:
            return
        if spec not in self.info.raw_imports:
            self.info.raw_imports.append(spec)
        parent = call.parent
        if parent is not None and parent.type == "variable_declarator":
            name = parent.child_by_field_name("name")
            if name is None:
                return
            if name.type == "identifier":
                self.info.bindings[self.text(name)] = (spec, "*")
            elif name.type == "object_pattern":
                for c in name.named_children:
                    if c.type == "shorthand_property_identifier_pattern":
                        self.info.bindings[self.text(c)] = (spec, self.text(c))
                    elif c.type == "pair_pattern":
                        key, val = c.child_by_field_name("key"), c.child_by_field_name("value")
                        if key is not None and val is not None and val.type == "identifier":
                            self.info.bindings[self.text(val)] = (spec, self.text(key))

    def entry_from_decorators(self, node) -> str:
        decos = [c for c in node.children if c.type == "decorator"]
        prev = node.prev_sibling
        while prev is not None and prev.type == "decorator":  # decorators are siblings in class bodies
            decos.append(prev)
            prev = prev.prev_sibling
        for c in decos:
            t = self.text(c)
            if _ROUTE_DECO.match(t):
                return f"HTTP route ({t.split('(')[0]})"
            if _HANDLER_DECO.match(t):
                return f"registered handler ({t.split('(')[0]})"
        return ""


def _make_symbol(cx: _Ctx, name: str, kind: str, node, body, parent, doc_node) -> Symbol:
    sym = Symbol(name, kind, node.start_point[0] + 1, node.end_point[0] + 1, cx.doc_above(doc_node),
                 cx.complexity(node), parent)
    if kind != "class" and body is not None:
        cx.walk_calls(body, sym.raw_calls)
    return sym


def _handle_class(cx: _Ctx, node, doc_node, out: list[Symbol]) -> str:
    name_node = node.child_by_field_name("name")
    name = cx.text(name_node) if name_node is not None else "default"
    cls = _make_symbol(cx, name, "class", node, None, None, doc_node)
    out.append(cls)
    body = node.child_by_field_name("body")
    for m in (body.named_children if body is not None else []):
        if m.type == "method_definition":
            mn, mb = m.child_by_field_name("name"), m.child_by_field_name("body")
            if mn is None or mb is None:
                continue
            sym = _make_symbol(cx, cx.text(mn), "method", m, mb, cls.qual, m)
            sym.entry = cx.entry_from_decorators(m)
            out.append(sym)
        elif m.type in ("public_field_definition", "field_definition"):
            mn, mv = m.child_by_field_name("name"), m.child_by_field_name("value")
            if mn is not None and mv is not None and mv.type in _FUNC_VALUES:
                sym = _make_symbol(cx, cx.text(mn), "method", m, mv, cls.qual, m)
                sym.entry = cx.entry_from_decorators(m)
                out.append(sym)
    return name


def _handle_stmt(cx: _Ctx, node, doc_node, out: list[Symbol], module_calls: list) -> str:
    """Process one top-level statement. Returns the declared name, if any."""
    t = node.type
    if t in ("function_declaration", "generator_function_declaration"):
        n, b = node.child_by_field_name("name"), node.child_by_field_name("body")
        name = cx.text(n) if n is not None else "default"
        out.append(_make_symbol(cx, name, "function", node, b, None, doc_node))
        return name
    if t in ("class_declaration", "abstract_class_declaration", "class"):
        return _handle_class(cx, node, doc_node, out)
    if t in ("lexical_declaration", "variable_declaration"):
        declared = ""
        for d in node.named_children:
            if d.type != "variable_declarator":
                continue
            n, v = d.child_by_field_name("name"), d.child_by_field_name("value")
            if n is not None and n.type == "identifier" and v is not None and v.type in _FUNC_VALUES:
                out.append(_make_symbol(cx, cx.text(n), "function", d, v, None, doc_node))
                declared = declared or cx.text(n)
            else:
                cx.walk_calls(d, module_calls)
        return declared
    if t == "import_statement":
        _handle_import(cx, node)
        return ""
    if t in ("function_signature", "interface_declaration", "type_alias_declaration", "enum_declaration",
             "comment", "empty_statement", "ambient_declaration"):
        return ""
    cx.walk_calls(node, module_calls)
    return ""


def _handle_import(cx: _Ctx, node) -> None:
    src = node.child_by_field_name("source")
    spec = cx.string(src) if src is not None else None
    for c in node.children:
        if c.type == "string" and spec is None:
            spec = cx.string(c)
    if not spec:
        return
    if spec not in cx.info.raw_imports:
        cx.info.raw_imports.append(spec)
    for c in node.children:
        if c.type == "import_clause":
            for part in c.children:
                if part.type == "identifier":
                    cx.info.bindings[cx.text(part)] = (spec, "default")
                elif part.type == "namespace_import":
                    ident = next((x for x in part.children if x.type == "identifier"), None)
                    if ident is not None:
                        cx.info.bindings[cx.text(ident)] = (spec, "*")
                elif part.type == "named_imports":
                    for sp in part.named_children:
                        if sp.type != "import_specifier":
                            continue
                        name, alias = sp.child_by_field_name("name"), sp.child_by_field_name("alias")
                        if name is not None:
                            cx.info.bindings[cx.text(alias or name)] = (spec, cx.text(name))
        elif c.type == "import_require_clause":
            ident = next((x for x in c.children if x.type == "identifier"), None)
            if ident is not None:
                cx.info.bindings[cx.text(ident)] = (spec, "*")


def analyze(info: FileInfo, source: str) -> bool:
    """Fill `info` from a tree-sitter parse. Returns False when unavailable."""
    if not AVAILABLE:
        return False
    data = source.encode("utf-8", "replace")
    try:
        root = _parser_for(info.path).parse(data).root_node
    except Exception as e:  # pragma: no cover - defensive
        info.parse_error = str(e)
        return False
    cx = _Ctx(info, data)
    info.analyzed, info.analyzer, info.has_calls = True, "tree-sitter", True
    info.complexity = cx.complexity(root) - 1
    module_calls: list = []
    for node in root.named_children:
        if node.type == "export_statement":
            src = node.child_by_field_name("source")
            if src is not None:  # re-export: counts as an import edge
                spec = cx.string(src)
                if spec not in info.raw_imports:
                    info.raw_imports.append(spec)
                continue
            is_default = any(c.type == "default" for c in node.children)
            decl, value = node.child_by_field_name("declaration"), node.child_by_field_name("value")
            if decl is not None:
                name = _handle_stmt(cx, decl, node, info.symbols, module_calls)
                if is_default and name:
                    info.default_export = name
            elif value is not None:
                if value.type == "identifier":
                    info.default_export = cx.text(value)
                elif value.type in _FUNC_VALUES:
                    info.symbols.append(_make_symbol(cx, "default", "function", value, value, None, node))
                    info.default_export = "default"
                elif value.type == "class":
                    info.default_export = _handle_class(cx, value, node, info.symbols)
                else:
                    cx.walk_calls(value, module_calls)
        else:
            _handle_stmt(cx, node, node, info.symbols, module_calls)
    if module_calls:
        mod = Symbol("<module>", "module", 1, max(1, source.count("\n") + 1), "", 1, None, qual="<module>")
        mod.raw_calls = module_calls
        info.symbols.insert(0, mod)
    return True
