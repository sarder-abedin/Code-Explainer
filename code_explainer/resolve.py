"""Turn raw import statements into edges between files inside the repo."""

from __future__ import annotations

import posixpath

from .model import FileInfo

_JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".json")


def _py_index(paths: set[str]) -> dict[str, str]:
    mods = []
    for p in paths:
        if not p.endswith(".py"):
            continue
        parts = p[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            mods.append((parts, p))
    index = {".".join(parts): p for parts, p in mods}
    # Allow dropping leading source-root directories (src/, lib/ ...) that are not
    # packages themselves, so `import pkg.mod` finds `src/pkg/mod.py`.
    for parts, p in sorted(mods, key=lambda m: len(m[0])):
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) + "/__init__.py" in paths:
                break
            index.setdefault(".".join(parts[i:]), p)
    return index


def _py_module_file(paths: set[str], base: str, dotted: str) -> str | None:
    rel = posixpath.join(base, *dotted.split(".")) if dotted else base
    for cand in (f"{rel}.py", f"{rel}/__init__.py"):
        if cand in paths:
            return cand
    return None


def _resolve_python(info: FileInfo, paths: set[str], index: dict[str, str]) -> set[str]:
    out: set[str] = set()
    for level, module, names in info.raw_imports:
        if level:
            base = posixpath.dirname(info.path)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            hits = [_py_module_file(paths, base, f"{module}.{n}" if module else n) for n in names]
            hits = [h for h in hits if h]
            if not hits:
                h = _py_module_file(paths, base, module)
                hits = [h] if h else []
            out.update(hits)
            continue
        matched = False
        for n in names:
            h = index.get(f"{module}.{n}")
            if h:
                out.add(h)
                matched = True
        if not matched or not names:
            parts = module.split(".")
            while parts:
                h = index.get(".".join(parts))
                if h:
                    out.add(h)
                    break
                parts.pop()
    return out


def _resolve_js(info: FileInfo, paths: set[str]) -> set[str]:
    out: set[str] = set()
    here = posixpath.dirname(info.path)
    for spec in info.raw_imports:
        if not spec.startswith("."):
            continue
        target = posixpath.normpath(posixpath.join(here, spec))
        candidates = [target]
        candidates += [target + ext for ext in _JS_EXTS]
        candidates += [f"{target}/index{ext}" for ext in _JS_EXTS]
        # TS projects import "./x.js" for "./x.ts".
        stem, ext = posixpath.splitext(target)
        if ext in (".js", ".jsx", ".mjs"):
            candidates += [stem + e for e in (".ts", ".tsx", ".mts")]
        for c in candidates:
            if c in paths and c != info.path:
                out.add(c)
                break
    return out


def resolve_imports(files: dict[str, FileInfo]) -> None:
    paths = set(files)
    index = _py_index(paths)
    for info in files.values():
        if info.lang == "Python":
            targets = _resolve_python(info, paths, index)
        elif info.lang in ("JavaScript", "TypeScript"):
            targets = _resolve_js(info, paths)
        else:
            continue
        targets.discard(info.path)
        info.imports = sorted(targets)
    for info in files.values():
        for t in info.imports:
            files[t].imported_by.append(info.path)
    for info in files.values():
        info.imported_by.sort()
