"""Which tests reach which code.

This is a static approximation, not a coverage run: starting from every function in a test file we follow the
call graph and note which functions of the project can be reached, and how directly. Calls that can only be
guessed from a method name ("maybe") are kept apart from calls that were followed through names and imports.
Dynamic dispatch, fixtures and mocks are invisible to it, so "untested" means "no test was seen reaching it".
"""

from __future__ import annotations

from collections import deque

from .analysis import CODE_LANGS
from .calls import sym_id
from .model import FileInfo

MAX_DEPTH = 10
LIST_CAP = 60
TESTS_CAP = 6
HOTSPOTS = 25
MIN_HOTSPOT_COMPLEXITY = 2


def _is_callable(sym) -> bool:
    if sym.kind not in ("function", "method"):
        return False
    return not (sym.name.startswith("__") and sym.name.endswith("__") and sym.name != "__init__")


def _reach(starts: list[str], graph: dict[str, list[tuple[str, bool]]], is_test_sym: set[str],
           with_guesses: bool) -> dict[str, int]:
    """Production symbol -> fewest calls from test code. Calls into other test code (helpers) are free."""
    dist: dict[str, int] = {}
    seen = {s: 0 for s in starts}
    queue = deque((s, 0) for s in starts)
    while queue:
        node, d = queue.popleft()
        if seen.get(node, 1 << 30) < d:
            continue
        for target, certain in graph.get(node, ()):
            if not certain and not with_guesses:
                continue
            free = target in is_test_sym
            nd = d if free else d + 1
            if nd > MAX_DEPTH or seen.get(target, 1 << 30) <= nd:
                continue
            seen[target] = nd
            if not free:
                dist[target] = nd
            (queue.appendleft if free else queue.append)((target, nd))
    return dist


def _risk(sym, callers: int, churn: int) -> int:
    return round(sym.complexity * (1 + callers) * (1 + min(churn, 20) / 10), 1)


def build_test_map(files: dict[str, FileInfo]) -> dict:
    tests = sorted(p for p, f in files.items() if f.is_test and f.analyzed and f.has_calls)
    measured = {p: f for p, f in files.items() if not f.is_test and f.lang in CODE_LANGS}
    sym_file: dict[str, tuple[str, object]] = {}
    graph: dict[str, list[tuple[str, bool]]] = {}
    callers: dict[str, int] = {}
    for path, f in files.items():
        for s in f.symbols:
            sid = sym_id(path, s)
            sym_file[sid] = (path, s)
            graph[sid] = [(c["id"], c["conf"] == "certain") for c in s.callees]
            for c in s.callees:
                if c["conf"] == "certain" and c["id"] != sid:
                    callers[c["id"]] = callers.get(c["id"], 0) + 1
    is_test_sym = {sid for sid, (p, _) in sym_file.items() if files[p].is_test}

    best: dict[str, int] = {}          # symbol -> fewest calls from any test (certain calls only)
    guessed: set[str] = set()          # reached only if guesses are believed
    reached_by: dict[str, set[str]] = {}
    for t in tests:
        starts = [sym_id(t, s) for s in files[t].symbols]
        sure = _reach(starts, graph, is_test_sym, False)
        maybe = _reach(starts, graph, is_test_sym, True)
        for sid, d in sure.items():
            best[sid] = min(best.get(sid, d), d)
            reached_by.setdefault(sid, set()).add(t)
        for sid in maybe:
            if sid not in sure:
                guessed.add(sid)
                reached_by.setdefault(sid, set()).add(t)
    guessed -= set(best)

    imported_by_tests = {p for p, f in measured.items() if any(files[i].is_test for i in f.imported_by)}
    out_files: dict[str, dict] = {}
    totals = {"direct": 0, "indirect": 0, "maybe": 0, "untested": 0}
    hotspots = []
    tested_files = untested_files = unknown_files = 0
    for path in sorted(measured):
        f = measured[path]
        syms = [s for s in f.symbols if _is_callable(s)]
        if not syms:
            continue
        if not (f.analyzed and f.has_calls):
            out_files[path] = {"state": "unknown", "tests": [], "direct": [], "indirect": [], "maybe": [], "untested": []}
            unknown_files += 1
            continue
        buckets: dict[str, list[str]] = {"direct": [], "indirect": [], "maybe": [], "untested": []}
        who: set[str] = set()
        for s in syms:
            sid = sym_id(path, s)
            if sid in best:
                key = "direct" if best[sid] == 1 else "indirect"
            elif sid in guessed:
                key = "maybe"
            else:
                key = "untested"
            buckets[key].append(s.qual)
            totals[key] += 1
            who |= reached_by.get(sid, set())
            if key == "untested" and s.complexity >= MIN_HOTSPOT_COMPLEXITY:
                hotspots.append({"path": path, "qual": s.qual, "line": s.line, "complexity": s.complexity,
                                 "callers": callers.get(sid, 0), "churn": f.churn, "risk": _risk(s, callers.get(sid, 0), f.churn)})
        reached = len(syms) - len(buckets["untested"])
        if reached == len(syms):
            state = "tested"
        elif reached:
            state = "partial"
        else:
            state = "imported" if path in imported_by_tests else "untested"
        tested_files += state in ("tested", "partial")
        untested_files += state in ("untested", "imported")
        entry = {"state": state, "tests": sorted(who)[:TESTS_CAP]}
        if len(who) > TESTS_CAP:
            entry["testsMore"] = len(who) - TESTS_CAP
        for key, names in buckets.items():
            entry[key] = names[:LIST_CAP]
            if len(names) > LIST_CAP:
                entry[key + "More"] = len(names) - LIST_CAP
        out_files[path] = entry
    total = sum(totals.values())
    hotspots.sort(key=lambda h: (-h["risk"], h["path"], h["line"]))
    return {
        "summary": {"testFiles": len(tests), "codeFiles": tested_files + untested_files, "testedFiles": tested_files,
                    "untestedFiles": untested_files, "unknownFiles": unknown_files, "symbols": total, **totals,
                    "pct": round(100 * (total - totals["untested"]) / total) if total else 0},
        "files": out_files,
        "hotspots": hotspots[:HOTSPOTS],
    }


def annotate_changes(data: dict) -> None:
    """For a change review: which added or modified functions no test was seen reaching."""
    ch = data.get("changes")
    if not ch:
        return
    per_file = (data.get("tests") or {}).get("files", {})
    count = 0
    for path, e in ch.get("files", {}).items():
        untested = set(per_file.get(path, {}).get("untested", []))
        sy = e.get("symbols", {})
        names = [q for q in sy.get("added", []) + sy.get("modified", []) if q in untested]
        e["untested"] = names
        count += len(names)
    ch.setdefault("summary", {})["untestedChanged"] = count
