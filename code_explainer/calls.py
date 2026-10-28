"""Resolve raw call sites into edges between symbols.

An edge is "certain" when the call can be followed through names and imports
(local function, imported function, `module.func`, `self.method`, `Class.method`)
and "inferred" when it is a guess from the method name alone (`obj.save()` when
exactly a few classes define `save`). Calls into code outside the repository
produce no edge.
"""

from __future__ import annotations

from collections import defaultdict

from .model import FileInfo, Symbol
from .resolve import js_target, py_module_target

_SELF_NAMES = {"self", "cls", "this"}
_INFER_LIMIT = 3


def sym_id(path: str, sym: Symbol) -> str:
    return f"{path}::{sym.qual}"


class _Resolver:
    def __init__(self, files: dict[str, FileInfo], index: dict[str, str]):
        self.files = files
        self.paths = set(files)
        self.index = index
        self.top: dict[str, dict[str, Symbol]] = {}
        self.methods: dict[str, dict[tuple[str, str], Symbol]] = {}
        self.by_method: dict[str, list[tuple[str, Symbol]]] = defaultdict(list)
        for path, info in files.items():
            top, meth = {}, {}
            for sym in info.symbols:
                if sym.kind in ("function", "class") and sym.parent is None:
                    top.setdefault(sym.name, sym)
                elif sym.kind == "method":
                    meth[(sym.parent, sym.name)] = sym
                    if not sym.name.startswith("__"):
                        self.by_method[sym.name].append((path, sym))
            self.top[path], self.methods[path] = top, meth
        # importable dotted name per python file (shortest = source roots dropped)
        self.dotted: dict[str, str] = {}
        self.prefixes: set[str] = set()
        for key, path in index.items():
            if path not in self.dotted or len(key) < len(self.dotted[path]):
                self.dotted[path] = key
            parts = key.split(".")
            for i in range(1, len(parts)):
                self.prefixes.add(".".join(parts[:i]))

    # --- targets -----------------------------------------------------------
    def _target(self, path: str, sym: Symbol) -> tuple[str, str]:
        """Classes resolve to their constructor so flows continue into it."""
        if sym.kind == "class":
            for ctor in ("__init__", "constructor"):
                m = self.methods[path].get((sym.qual, ctor))
                if m:
                    return path, sym_id(path, m)
        return path, sym_id(path, sym)

    def _binding_file(self, info: FileInfo, name: str) -> tuple[str | None, str | None, bool]:
        """(file, attr, known) for an imported name. known=False means unbound."""
        b = info.bindings.get(name)
        if b is None:
            return None, None, False
        if info.lang == "Python":
            level, module, attr = b
            return py_module_target(info.path, self.paths, self.index, level, module), attr, True
        spec, attr = b
        return js_target(info.path, spec, self.paths), attr, True

    def lookup_name(self, info: FileInfo, name: str, depth: int = 0) -> tuple[str, str] | None:
        """A bare `name(...)` call."""
        local = self.top[info.path].get(name)
        if local:
            return self._target(info.path, local)
        if depth > 3:
            return None
        file, attr, known = self._binding_file(info, name)
        if not known or file is None or attr in (None, "*"):
            return None
        if attr == "default":
            attr = self.files[file].default_export
            if not attr:
                return None
        sym = self.top[file].get(attr)
        if sym:
            return self._target(file, sym)
        return self.lookup_name(self.files[file], attr, depth + 1)  # re-export

    # --- chains ------------------------------------------------------------
    def _start_state(self, info: FileInfo, head: str):
        """State after resolving the first name: ('mod', F) | ('cls', F, C) | ('pkg', dotted) | 'external' | None."""
        cls = self.top[info.path].get(head)
        if cls is not None:
            return ("cls", info.path, cls.qual) if cls.kind == "class" else None
        file, attr, known = self._binding_file(info, head)
        if not known:
            return None
        if info.lang == "Python":
            level, module, _ = info.bindings[head]
            if attr is None:  # `import a.b as x` / `import a`
                if file is not None:
                    return ("mod", file)
                if not level and (module in self.prefixes or module in self.index):
                    return ("pkg", module)
                return "external"
            sub = py_module_target(info.path, self.paths, self.index, level, f"{module}.{attr}" if module else attr)
            if sub:
                return ("mod", sub)
            if file is None:
                return "external"
        else:
            if file is None:
                return "external"
            if attr == "*":
                return ("mod", file)
            if attr == "default":
                attr = self.files[file].default_export
        sym = self.top[file].get(attr or "")
        if sym is not None and sym.kind == "class":
            return ("cls", file, sym.qual)
        return None

    def resolve_chain(self, info: FileInfo, sym: Symbol, chain: list[str]) -> list[tuple[str, str, str]]:
        """-> [(file, symbol id, confidence)]"""
        head, last = chain[0], chain[-1]
        if len(chain) == 1:
            t = self.lookup_name(info, head)
            return [(t[0], t[1], "certain")] if t else []
        if head in _SELF_NAMES and len(chain) == 2 and sym.parent:
            m = self.methods[info.path].get((sym.parent, last))
            if m:
                return [(info.path, sym_id(info.path, m), "certain")]
            return self._infer(last)
        state = self._start_state(info, head)
        if state == "external":
            return []
        if state is not None:
            for i, part in enumerate(chain[1:], start=1):
                is_last = i == len(chain) - 1
                if state is None:
                    break
                kind = state[0]
                if kind == "mod":
                    file = state[1]
                    if is_last:
                        t = self.lookup_name(self.files[file], part)
                        if t:
                            return [(t[0], t[1], "certain")]
                        state = None
                    else:
                        c = self.top[file].get(part)
                        if c is not None and c.kind == "class":
                            state = ("cls", file, c.qual)
                        else:
                            dotted = self.dotted.get(file)
                            sub = self.index.get(f"{dotted}.{part}") if dotted else None
                            state = ("mod", sub) if sub else None
                elif kind == "cls":
                    _, file, cname = state
                    m = self.methods[file].get((cname, part))
                    if m and is_last:
                        return [(file, sym_id(file, m), "certain")]
                    state = None
                elif kind == "pkg":
                    key = f"{state[1]}.{part}"
                    if key in self.index:
                        state = ("mod", self.index[key])
                    elif key in self.prefixes:
                        state = ("pkg", key)
                    else:
                        state = None
        if head in info.bindings:  # imported something we could not follow
            return []
        return self._infer(last)

    def _infer(self, name: str) -> list[tuple[str, str, str]]:
        cands = self.by_method.get(name, [])
        if 1 <= len(cands) <= _INFER_LIMIT:
            return [(p, sym_id(p, s), "inferred") for p, s in cands]
        return []


def resolve_calls(files: dict[str, FileInfo], index: dict[str, str]) -> dict:
    r = _Resolver(files, index)
    certain = inferred = 0
    for info in files.values():
        if not info.has_calls:
            continue
        for sym in info.symbols:
            found: dict[str, dict] = {}
            for chain, line in sym.raw_calls:
                for _file, tid, conf in r.resolve_chain(info, sym, chain):
                    prev = found.get(tid)
                    if prev is None:
                        found[tid] = {"id": tid, "conf": conf, "line": line}
                    elif prev["conf"] == "inferred" and conf == "certain":
                        prev["conf"] = "certain"
            sym.callees = list(found.values())
            certain += sum(1 for c in sym.callees if c["conf"] == "certain")
            inferred += sum(1 for c in sym.callees if c["conf"] == "inferred")
    return {"certain": certain, "inferred": inferred}
