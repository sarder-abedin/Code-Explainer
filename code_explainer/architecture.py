"""The architecture map: the parts of a project, how they connect, and what they talk to.

Components are found from the folder layout (or, for a flat project, from which files import each other). Arrows are
the imports (and calls) that cross from one component to another. Outside systems (a database, the web, other
programs...) are recognised from the libraries a file imports, using a fixed table, so nothing is guessed by a model.
"""

from __future__ import annotations

import posixpath
from collections import Counter

from . import flowlayout, graphs
from .libkinds import KINDS, LIBRARIES, _KIND_OF, kind_of  # noqa: F401  (re-exported)
from .analysis import CODE_LANGS
from .model import FileInfo

MAX_COMPONENTS = 14
MAX_EXTERNALS = 12
TOP_FILES = 6
FILES_AS_COMPONENTS = 14       # a flat project this small gets one box per file
COMPONENT_SIZE = (210, 70)
EXTERNAL_SIZE = (180, 52)
EXAMPLES = 5
_OTHER = "(other)"
_ROOT = "(top level)"

# the table of outside systems lives in libkinds.py (shared with the function contracts)


# ----------------------------------------------------------------- finding the components
def _folder_groups(code: dict[str, FileInfo]) -> dict[str, tuple] | None:
    dirs = {p: p.split("/")[:-1] for p in code}
    prefix: list[str] = []
    for parts in zip(*dirs.values()):
        if len(set(parts)) != 1:
            break
        prefix.append(parts[0])
    rel = {p: d[len(prefix):] for p, d in dirs.items()}
    for depth in range(1, 5):
        groups = {p: tuple(rel[p][:depth]) for p in code}
        n = len(set(groups.values()))
        if n >= 3:
            if n > MAX_COMPONENTS:
                size = Counter(groups.values())
                keep = {g for g, _ in sorted(size.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_COMPONENTS - 1]}
                groups = {p: g if g in keep else (_OTHER,) for p, g in groups.items()}
            return {p: (tuple(prefix), g) for p, g in groups.items()}
    return None


def _clusters(code: dict[str, FileInfo], neighbours: dict[str, set[str]]) -> tuple[dict[str, str], dict[str, str]]:
    """Group files that import each other (label propagation, deterministic). Returns (file -> id, id -> name)."""
    paths = sorted(code)
    label = {p: i for i, p in enumerate(paths)}
    for _ in range(12):
        changed = False
        for p in paths:
            counts = Counter(label[q] for q in neighbours[p])
            if not counts:
                continue
            top = max(counts.values())
            best = label[p] if counts.get(label[p]) == top else min(l for l, c in counts.items() if c == top)
            if best != label[p]:
                label[p], changed = best, True
        if not changed:
            break
    members: dict[int, list[str]] = {}
    for p in paths:
        members.setdefault(label[p], []).append(p)
    groups = {l: list(m) for l, m in members.items() if len(m) >= 2}
    loners: list[str] = []
    for m in members.values():
        if len(m) < 2:                                         # a loner joins the group it imports most, else (other)
            near = Counter(label[q] for q in neighbours[m[0]] if label[q] in groups)
            if near:
                groups[min(near, key=lambda k: (-near[k], k))].extend(m)
            else:
                loners.extend(m)
    ranked = sorted(groups.values(), key=lambda m: (-len(m), min(m)))
    keep, rest = ranked[:MAX_COMPONENTS - 1], ranked[MAX_COMPONENTS - 1:]
    assign: dict[str, str] = {}
    names: dict[str, str] = {}
    for m in keep:
        cid = f"cluster:{min(m)}"
        top_file = max(m, key=lambda p: (len(code[p].imported_by), p))
        names[cid] = posixpath.splitext(posixpath.basename(top_file))[0] + " group"
        assign.update({p: cid for p in m})
    for p in loners + [p for m in rest for p in m]:
        assign[p], names[_OTHER] = _OTHER, _OTHER
    return assign, names


def _group_files(code: dict[str, FileInfo], neighbours: dict[str, set[str]]) -> tuple[dict[str, str], dict[str, str], str]:
    """(file -> component id, id -> name, how they were grouped)"""
    folders = _folder_groups(code)
    if folders is not None:
        ids: dict[str, str] = {}
        paths: dict[str, tuple] = {}
        for p, (prefix, g) in folders.items():
            cid = _OTHER if g == (_OTHER,) else "/".join([*prefix, *g]) if g else _ROOT
            ids[p] = cid
            paths[cid] = g
        names = {}
        last = Counter(posixpath.basename(c) for c in paths)
        for cid, g in paths.items():
            names[cid] = cid if cid in (_OTHER, _ROOT) else (g[-1] if last[g[-1]] == 1 else "/".join(g))
        return ids, names, "folders"
    if len(code) <= FILES_AS_COMPONENTS:
        stems = Counter(posixpath.splitext(posixpath.basename(p))[0] for p in code)
        names = {p: (posixpath.splitext(posixpath.basename(p))[0] if stems[posixpath.splitext(posixpath.basename(p))[0]] == 1 else p) for p in code}
        return {p: p for p in code}, names, "files"
    assign, names = _clusters(code, neighbours)
    return assign, names, "clusters"


# ----------------------------------------------------------------- outside systems
def _external_modules(info: FileInfo, local: set[str]) -> set[tuple[str, str]]:
    """(kind, library) for each outside library this file imports."""
    found = set()
    for raw in info.raw_imports:
        if isinstance(raw, (tuple, list)):
            level, module = raw[0], raw[1]
            if level or not module:
                continue
            name = module
        else:
            name = str(raw)
            if name.startswith((".", "/")):
                continue
            name = name[5:] if name.startswith("node:") else name
            parts = name.split("/")
            name = "/".join(parts[:2]) if name.startswith("@") else parts[0]
        if name.split(".")[0] in local:
            continue
        hit = kind_of(name)
        if hit:
            found.add(hit)
    return found


# ----------------------------------------------------------------- the whole map
def _is_marker(path: str, info: FileInfo) -> bool:
    """An empty `__init__.py`: it only marks a folder as a package, so it says nothing about the design."""
    return posixpath.basename(path) == "__init__.py" and not (info.symbols or info.imports or info.doc)


def _count(entry: dict, key: str) -> int:
    return len(entry.get(key, [])) + entry.get(key + "More", 0)


def build(files: dict[str, FileInfo], tests: dict | None = None, hygiene: dict | None = None) -> dict:
    code = {p: f for p, f in files.items() if not f.is_test and f.lang in CODE_LANGS and not _is_marker(p, f)}
    empty = {"components": [], "externals": [], "edges": [], "fileMap": {}, "w": 0, "h": 0,
             "summary": {"components": 0, "externals": 0, "cycles": 0, "grouping": "none"}}
    if not code:
        return empty
    neighbours: dict[str, set[str]] = {p: set() for p in code}
    for p, f in code.items():
        for t in f.imports:
            if t in code and t != p:
                neighbours[p].add(t)
                neighbours[t].add(p)
    comp_of, names, grouping = _group_files(code, neighbours)
    members: dict[str, list[str]] = {}
    for p in sorted(code):
        members.setdefault(comp_of[p], []).append(p)

    local = set()
    for p in code:
        local.update(p.split("/")[:-1])
        local.add(posixpath.splitext(posixpath.basename(p))[0])

    test_files = (tests or {}).get("files", {})
    findings = (hygiene or {}).get("findings", [])         # test files are not components, so their findings never match
    components = []
    for cid, paths in members.items():
        infos = [code[p] for p in paths]
        by_use = sorted(paths, key=lambda p: (-len(code[p].imported_by), p))
        docs = [code[p].doc for p in sorted(paths, key=lambda p: (posixpath.basename(p) != "__init__.py", -len(code[p].imported_by), p)) if code[p].doc]
        symbols = reached = 0
        for p in paths:
            e = test_files.get(p)
            if e:
                total = sum(_count(e, k) for k in ("direct", "indirect", "maybe", "untested"))
                symbols += total
                reached += total - _count(e, "untested")
        mine = [f for f in findings if f["path"] in set(paths)]
        components.append({
            "id": cid, "name": names[cid], "path": cid if grouping == "folders" and cid not in (_OTHER, _ROOT) else "",
            "files": len(paths), "loc": sum(f.loc for f in infos), "complexity": sum(f.complexity for f in infos),
            "entry": any(f.entry_reasons for f in infos), "doc": docs[0] if docs else "", "top": by_use[:TOP_FILES],
            "cycle": False, "tests": {"symbols": symbols, "reached": reached},
            "findings": {"high": sum(f["sev"] == "high" for f in mine), "medium": sum(f["sev"] == "medium" for f in mine), "total": len(mine)}})
    components.sort(key=lambda c: (not c["entry"], -c["loc"], c["id"]))

    pair_imports: dict[tuple[str, str], list] = {}
    pair_calls: Counter = Counter()
    for p, f in code.items():
        for t in f.imports:
            if t in code and comp_of[t] != comp_of[p]:
                pair_imports.setdefault((comp_of[p], comp_of[t]), []).append((p, t))
        for s in f.symbols:
            for c in s.callees:
                target = c["id"].split("::")[0]
                if c["conf"] == "certain" and target in code and comp_of[target] != comp_of[p]:
                    pair_calls[(comp_of[p], comp_of[target])] += 1
    ids = [c["id"] for c in components]
    adj = {i: sorted({b for (a, b) in set(pair_imports) | set(pair_calls) if a == i}) for i in ids}
    loops = graphs.cycles(ids, adj)
    cyclic = {i for comp in loops for i in comp}
    for c in components:
        c["cycle"] = c["id"] in cyclic
    edges = []
    for key in sorted(set(pair_imports) | set(pair_calls)):
        pairs = sorted(set(pair_imports.get(key, [])))
        edges.append({"from": key[0], "to": key[1], "imports": len(pairs), "calls": pair_calls.get(key, 0), "files": 0,
                      "cycle": any(key[0] in c and key[1] in c for c in loops),
                      "examples": [list(x) for x in pairs[:EXAMPLES]]})

    uses: dict[str, dict] = {}
    for p, f in code.items():
        for kind, lib in sorted(_external_modules(f, local)):
            u = uses.setdefault(kind, {"modules": set(), "files": set(), "by": {}})
            u["modules"].add(lib)
            u["files"].add(p)
            u["by"].setdefault(comp_of[p], set()).add((p, lib))
    ranked = sorted(uses.items(), key=lambda kv: (-len(kv[1]["files"]), kv[0]))[:MAX_EXTERNALS]
    externals = [{"id": f"x:{kind}", "kind": kind, "label": KINDS[kind], "modules": sorted(u["modules"])[:5], "files": len(u["files"])}
                 for kind, u in ranked]
    for kind, u in ranked:
        for cid, used in sorted(u["by"].items()):
            edges.append({"from": cid, "to": f"x:{kind}", "imports": len({lib for _, lib in used}), "calls": 0,
                          "files": len({p for p, _ in used}), "cycle": False, "examples": [list(x) for x in sorted(used)[:EXAMPLES]]})

    index = {b["id"]: i for i, b in enumerate(components + externals)}
    sizes = {index[c["id"]]: COMPONENT_SIZE for c in components}
    sizes.update({index[x["id"]]: EXTERNAL_SIZE for x in externals})
    pairs = [(index[e["from"]], index[e["to"]]) for e in edges]
    res = flowlayout.layout(sizes, pairs, order=sorted(index.values()))
    for b in components + externals:
        i = index[b["id"]]
        b["x"], b["y"] = res["pos"][i]
        b["w"], b["h"] = sizes[i]
    for e in edges:
        key = (index[e["from"]], index[e["to"]])
        e["path"] = [[x, y] for x, y in res["paths"][key]]
        e["back"] = key in res["back"]
        (x0, y0), (x1, y1) = e["path"][0], e["path"][1]
        e["lx"], e["ly"] = round((x0 + x1) / 2), round((y0 + y1) / 2)
    return {"components": components, "externals": externals, "edges": edges, "fileMap": dict(comp_of),
            "w": res["width"], "h": res["height"],
            "summary": {"components": len(components), "externals": len(externals), "cycles": len(loops), "grouping": grouping}}
