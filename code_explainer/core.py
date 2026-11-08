"""Orchestrates ingest -> analysis -> resolution -> metrics into one report dict."""

from __future__ import annotations

import contextlib
import json
import posixpath
import re
from collections import deque
from pathlib import Path

from . import __version__
from .analysis import CODE_LANGS, analyze_file, detect_language
from .ingest import git_churn, git_summary, list_files
from .model import FileInfo
from .explain import explain_repo
from .calls import resolve_calls, sym_id
from .diff import compare, resolve_ref, snapshot, story_steps
from .duplicates import find_duplicates
from . import contract
from .flowgraph import apply_budget
from . import architecture
from .hygiene import scan as hygiene_scan
from .testmap import annotate_changes, build_test_map
from .resolve import py_index, resolve_imports

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: use the backport that pyproject.toml requires there
    import tomli as tomllib

MAX_SOURCE_BYTES = 300_000
MAX_TOTAL_SOURCE_BYTES = 25_000_000
_TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|specs)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$|(^|/)conftest\.py$")
_ENTRY_STEMS = {"main", "app", "cli", "server", "manage", "wsgi", "asgi", "__main__", "run", "index"}
_NEVER_UNUSED = {"__init__.py", "setup.py", "conftest.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _declared_entry_points(root: Path, files: dict[str, FileInfo]) -> None:
    """Entry points declared in pyproject.toml / package.json."""
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(_read(pyproject))
        except Exception:
            data = {}
        scripts = {**data.get("project", {}).get("scripts", {}),
                   **data.get("tool", {}).get("poetry", {}).get("scripts", {})}
        for cmd, target in scripts.items():
            module = str(target).split(":")[0].strip().split("[")[0]
            parts = module.split(".")
            hit = None
            for i in range(len(parts)):  # tolerate src/ layouts
                for cand in ("/".join(parts[i:]) + ".py", "/".join(parts[i:]) + "/__init__.py"):
                    hit = next((p for p in files if p == cand or p.endswith("/" + cand)), None)
                    if hit:
                        break
                if hit:
                    break
            if hit:
                files[hit].entry_reasons.append(f"console script `{cmd}` (pyproject.toml)")
                attr = str(target).split(":")[1].strip() if ":" in str(target) else ""
                for sym in files[hit].symbols:
                    if attr and sym.qual == attr and not sym.entry:
                        sym.entry = f"console script `{cmd}`"
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(_read(pkg))
        except ValueError:
            return
        declared = []
        if isinstance(data.get("main"), str):
            declared.append(("main", data["main"]))
        bins = data.get("bin")
        if isinstance(bins, str):
            declared.append(("bin", bins))
        elif isinstance(bins, dict):
            declared += [(f"bin `{k}`", v) for k, v in bins.items() if isinstance(v, str)]
        for label, rel in declared:
            p = posixpath.normpath(rel)
            for cand in (p, p + ".js", p + "/index.js"):
                if cand in files:
                    files[cand].entry_reasons.append(f"package.json {label}")
                    break


def _mark_entries_and_tests(files: dict[str, FileInfo]) -> None:
    for info in files.values():
        info.is_test = bool(_TEST_RE.search(info.path))
        stem = posixpath.splitext(posixpath.basename(info.path))[0]
        depth = info.path.count("/")
        if (info.lang in CODE_LANGS and not info.is_test and depth <= 2
                and stem in _ENTRY_STEMS and not info.entry_reasons):
            info.entry_reasons.append("file name suggests an entry point")


def _mark_unused(files: dict[str, FileInfo]) -> None:
    for info in files.values():
        info.possibly_unused = (
            info.lang in CODE_LANGS and info.analyzed and not info.is_test
            and not info.imported_by and not info.entry_reasons
            and posixpath.basename(info.path) not in _NEVER_UNUSED
        )


def _reading_order(files: dict[str, FileInfo], limit: int = 25) -> list[dict]:
    """Entry points first, then what they depend on, most-depended-on first."""
    code = {p: f for p, f in files.items() if f.lang in CODE_LANGS and not f.is_test}
    entries = sorted((p for p, f in code.items() if f.entry_reasons),
                     key=lambda p: (-len(code[p].imports), p))
    order: list[dict] = []
    seen: set[str] = set()
    queue = deque()
    for p in entries:
        seen.add(p)
        order.append({"path": p, "why": "Entry point: " + code[p].entry_reasons[0]})
        queue.append(p)
    while queue and len(order) < limit:
        cur = queue.popleft()
        for dep in sorted(code[cur].imports if cur in code else [],
                          key=lambda d: (-len(files[d].imported_by), d)):
            if dep in seen or dep not in code:
                continue
            seen.add(dep)
            n = len(files[dep].imported_by)
            order.append({"path": dep, "why": f"Used by {n} file{'s' if n != 1 else ''}; imported from {posixpath.basename(cur)}"})
            queue.append(dep)
            if len(order) >= limit:
                break
    if not order:  # no entry points found: fall back to the most central files
        for p in sorted(code, key=lambda p: (-len(code[p].imported_by), p))[:limit]:
            if code[p].imported_by:
                order.append({"path": p, "why": f"Imported by {len(code[p].imported_by)} files"})
    return order


_ROOT_FUNCS = {"main", "run", "cli", "handler", "start", "serve"}


def _flow_roots(files: dict[str, FileInfo]) -> list[dict]:
    """Places where execution can start: commands, routes, scripts, main()."""
    roots: dict[str, dict] = {}
    rank = {"console": 0, "route": 1, "handler": 2, "script": 3, "main": 4}

    def add(path: str, sym, why: str, kind: str) -> None:
        sid = sym_id(path, sym)
        if sid not in roots or rank[kind] < rank[roots[sid]["kind"]]:
            roots[sid] = {"id": sid, "path": path, "name": sym.qual if sym.qual != "<module>" else base_name(path),
                          "why": why, "kind": kind}

    def base_name(p: str) -> str:
        return posixpath.basename(p)

    for path, info in files.items():
        if info.is_test or not info.has_calls:
            continue
        for sym in info.symbols:
            if sym.entry:
                kind = "console" if sym.entry.startswith("console") else "route" if "route" in sym.entry else "handler"
                add(path, sym, sym.entry, kind)
            elif info.entry_reasons and sym.kind == "module" and sym.callees:
                add(path, sym, info.entry_reasons[0], "script")
            elif info.entry_reasons and sym.kind == "function" and sym.name in _ROOT_FUNCS and sym.callees:
                add(path, sym, f"`{sym.name}()` in an entry file", "main")
    return sorted(roots.values(), key=lambda r: (rank[r["kind"]], r["path"], r["name"]))


def _build(root: Path, name: str, churn_root: Path | None, explain: bool = True) -> tuple[dict, dict[str, FileInfo]]:
    """Analyse one snapshot of a repository on disk."""
    root = Path(root)
    files: dict[str, FileInfo] = {}
    budget = MAX_TOTAL_SOURCE_BYTES
    for rel in list_files(root):
        try:
            text = _read(root / rel)
        except OSError:
            continue
        info = analyze_file(rel, detect_language(rel), text)
        info.size = len(text.encode("utf-8", errors="replace"))
        if info.size <= MAX_SOURCE_BYTES and budget >= info.size:
            info.source = text
            budget -= info.size
        else:
            info.truncated = True
        files[rel] = info

    churn = git_churn(churn_root) if churn_root else None
    if churn:
        for p, n in churn.items():
            if p in files:
                files[p].churn = n

    flow_trimmed = apply_budget(files)
    resolve_imports(files)
    call_stats = resolve_calls(files, py_index(set(files)))
    contract.finish(files)
    contract_trimmed = contract.apply_budget(files)
    _declared_entry_points(root, files)
    _mark_entries_and_tests(files)
    _mark_unused(files)

    langs: dict[str, dict] = {}
    for f in files.values():
        entry = langs.setdefault(f.lang, {"files": 0, "loc": 0})
        entry["files"] += 1
        entry["loc"] += f.loc
    edges = [[f.path, t] for f in files.values() for t in f.imports]
    repo = {
        "name": name,
        "files": len(files),
        "loc": sum(f.loc for f in files.values()),
        "languages": langs,
        "hasHistory": churn is not None,
        "git": git_summary(churn_root) if churn_root else {},
    }
    reading_order = _reading_order(files)
    flow_roots = _flow_roots(files)
    explained = explain_repo(files, flow_roots, reading_order, repo) if explain else {}
    data = {
        "version": __version__,
        "repo": repo,
        "files": [files[p].to_dict() for p in sorted(files)],
        "edges": edges,
        "readingOrder": reading_order,
        "flowRoots": flow_roots,
        "explain": explained,
        "callStats": call_stats,
        "duplicates": find_duplicates(files)[:25],
        "flowTrimmed": flow_trimmed,
        "contractTrimmed": contract_trimmed,
    }
    if explain:   # the base snapshot of a change review only needs the structure
        data["tests"] = build_test_map(files)
        data["hygiene"] = hygiene_scan(files)
        data["architecture"] = architecture.build(files, data["tests"], data["hygiene"])
    return data, files


def analyze_repo(root: Path, name: str | None = None, base: str | None = None, head: str | None = None) -> dict:
    """Analyse a repository. With `base`, also review the change from `base` to
    `head` (or to the working tree when `head` is not given)."""
    root = Path(root)
    name = name or root.name
    with contextlib.ExitStack() as stack:
        src = root
        if head:
            resolve_ref(root, head)
            src = stack.enter_context(snapshot(root, head))
        data, files = _build(src, name, churn_root=root)
        if base:
            resolve_ref(root, base)
            base_root = stack.enter_context(snapshot(root, base))
            _, base_files = _build(base_root, name, churn_root=None, explain=False)
            changes = compare(root, base, head, base_files, files, data["flowRoots"])
            data["changes"] = changes
            data["explain"]["story"][1:1] = story_steps(changes)
            annotate_changes(data)
    return data
