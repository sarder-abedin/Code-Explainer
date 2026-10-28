"""Review of a change: what moved, what it can break, what to read first.

Compares two analysed snapshots of a repository (a git ref against another ref or
against the working tree) and works out, for the people reading the change:

* which files and symbols were added, removed or modified;
* the blast radius: who depends on the changed code;
* which entry points (commands, routes, scripts) can reach the changed code;
* leftovers: new code nothing calls, code that lost its last caller, removed code
  that something still uses;
* new copy-pasted functions and complexity increases;
* a suggested reading order.
"""

from __future__ import annotations

import io
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path

from .duplicates import find_duplicates, symbol_hashes
from .explain import _list, _plural
from .ingest import IngestError, _ignored, _is_binary
from .model import FileInfo

MAX_PATCH_BYTES = 60_000
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_HDR = re.compile(r"^diff --git a/(.+) b/(.+)$")


def _git(root: Path, *args: str, timeout: int = 120, binary: bool = False):
    try:
        out = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=off", *args],
                             capture_output=True, timeout=timeout, check=True)
    except subprocess.CalledProcessError as e:
        raise IngestError(f"git {' '.join(args[:2])} failed: {e.stderr.decode('utf-8', 'replace').strip()}") from e
    except (OSError, subprocess.TimeoutExpired) as e:
        raise IngestError(f"git {' '.join(args[:2])} failed: {e}") from e
    return out.stdout if binary else out.stdout.decode("utf-8", "replace")


def resolve_ref(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()


@contextmanager
def snapshot(root: Path, ref: str):
    """Extract the tree at `ref` into a temp dir (regular files only)."""
    data = _git(root, "archive", "--format=tar", ref, binary=True, timeout=300)
    tmp = Path(tempfile.mkdtemp(prefix="code-explainer-snap-"))
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for m in tar.getmembers():
                name = posixpath.normpath(m.name)
                if name.startswith(("/", "..")) or not (m.isfile() or m.isdir()):
                    continue  # symlinks and odd entries never leave the repo's own content
                m.name = name
                try:
                    tar.extract(m, tmp, filter="data")
                except TypeError:  # Python without extraction filters
                    tar.extract(m, tmp)
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ patches


def parse_patch(text: str) -> dict[str, dict]:
    """Parse `git diff` output. `changed` holds the new-side line numbers that were
    added or sit right next to a deletion (context lines are not counted)."""
    files: dict[str, dict] = {}
    cur = None
    lines_in: list[str] = []
    in_hunk = False
    new_ln = 0

    def finish():
        if cur is not None:
            patch = "\n".join(lines_in)
            cur["patch"] = patch[:MAX_PATCH_BYTES] + ("\n… (patch truncated)" if len(patch) > MAX_PATCH_BYTES else "")

    for line in text.split("\n"):
        m = _DIFF_HDR.match(line)
        if m and not (in_hunk and not line.startswith("diff --git a/")):
            finish()
            old, new = m.group(1), m.group(2)
            cur = {"path": new, "oldPath": old, "status": "M", "added": 0, "deleted": 0, "changed": set(), "binary": False}
            files[new] = cur
            lines_in, in_hunk = [], False
            continue
        if cur is None:
            continue
        h = _HUNK.match(line)
        if h:
            in_hunk, new_ln = True, int(h.group(3))
            lines_in.append(line)
            continue
        if not in_hunk:
            if line.startswith("new file mode"):
                cur["status"] = "A"
            elif line.startswith("deleted file mode"):
                cur["status"] = "D"
            elif line.startswith("rename from "):
                cur["status"], cur["oldPath"] = "R", line[len("rename from "):]
            elif line.startswith(("Binary files", "GIT binary patch")):
                cur["binary"] = True
            continue
        if line.startswith("+"):
            cur["added"] += 1
            cur["changed"].add(new_ln)
            new_ln += 1
        elif line.startswith("-"):
            cur["deleted"] += 1
            cur["changed"].add(max(1, new_ln - 1))
        elif line.startswith(" "):
            new_ln += 1
        lines_in.append(line)
    finish()
    return files


def collect_changes(root: Path, base: str, head: str | None) -> dict[str, dict]:
    args = ["diff", "--no-color", "--no-ext-diff", "-M", "-U3", base] + ([head] if head else [])
    changes = parse_patch(_git(root, *args))
    if head is None:  # uncommitted new files are part of "the working tree"
        out = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
        for rel in [p for p in out.split("\0") if p]:
            full = root / rel
            if rel in changes or not full.is_file() or full.is_symlink() or _is_binary(full) or full.stat().st_size > 200_000:
                continue
            text = full.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            patch = "\n".join(f"+{ln}" for ln in lines)
            changes[rel] = {"path": rel, "oldPath": rel, "status": "A", "added": len(lines), "deleted": 0,
                            "changed": set(range(1, len(lines) + 1)), "binary": False,
                            "patch": f"@@ -0,0 +1,{len(lines)} @@\n{patch}"[:MAX_PATCH_BYTES]}
    return {p: c for p, c in changes.items() if not _ignored(p) and not (c["binary"] and c["status"] != "D")}


# ------------------------------------------------------------------ analysis


def _sid(path: str, qual: str) -> str:
    return f"{path}::{qual}"


def _reverse_calls(files: dict[str, FileInfo]) -> dict[str, dict[str, str]]:
    rev: dict[str, dict[str, str]] = defaultdict(dict)
    for path, info in files.items():
        for sym in info.symbols:
            me = _sid(path, sym.qual)
            for c in sym.callees:
                if c["id"] != me and rev[c["id"]].get(me) != "certain":
                    rev[c["id"]][me] = c["conf"]
    return rev


def _bfs(start: set[str], edges: dict[str, dict | list], depth: int) -> dict[str, int]:
    dist = {s: 0 for s in start}
    q = deque(start)
    while q:
        cur = q.popleft()
        if dist[cur] >= depth:
            continue
        for nxt in edges.get(cur, ()):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    return dist


def _touched(changed: set[int], line: int, end: int) -> bool:
    return any(line <= n <= end for n in changed)


def compare(root: Path, base_ref: str, head_ref: str | None, base_files: dict[str, FileInfo],
            head_files: dict[str, FileInfo], flow_roots: list[dict]) -> dict:
    raw = collect_changes(root, base_ref, head_ref)
    head_rev, base_rev = _reverse_calls(head_files), _reverse_calls(base_files)
    head_hashes, base_hashes = symbol_hashes(head_files), symbol_hashes(base_files)

    entries: dict[str, dict] = {}
    added_ids: set[str] = set()
    modified_ids: set[str] = set()
    removed: list[tuple[str, str]] = []  # (base path, qual)
    for path, c in raw.items():
        old = c["oldPath"] if c["status"] in ("R", "D") else path
        new_info, old_info = head_files.get(path), base_files.get(old)
        before = {s.qual: s for s in old_info.symbols if s.kind != "module"} if old_info else {}
        after = {s.qual: s for s in new_info.symbols if s.kind != "module"} if new_info else {}
        sym_added = sorted(q for q in after if q not in before)
        sym_removed = sorted(q for q in before if q not in after)
        sym_modified = sorted(q for q, s in after.items() if q in before and (
            c["status"] == "A" or _touched(c["changed"], s.line, s.end_line)))
        added_ids |= {_sid(path, q) for q in sym_added}
        modified_ids |= {_sid(path, q) for q in sym_modified}
        removed += [(old, q) for q in sym_removed]
        entries[path] = {
            "path": path, "oldPath": old if old != path else "", "status": c["status"],
            "added": c["added"], "deleted": c["deleted"],
            "lang": (new_info or old_info).lang if (new_info or old_info) else "",
            "complexityBefore": old_info.complexity if old_info else 0,
            "complexityAfter": new_info.complexity if new_info else 0,
            "isTest": bool((new_info or old_info).is_test) if (new_info or old_info) else False,
            "symbols": {"added": sym_added, "removed": sym_removed, "modified": sym_modified},
            "patch": c["patch"], "binary": c["binary"], "analyzed": bool(new_info and new_info.analyzed),
        }

    changed_syms = added_ids | modified_ids
    file_importers = {p: i.imported_by for p, i in head_files.items()}

    # blast radius: who calls / imports the changed code
    sym_dist = _bfs(changed_syms, head_rev, 3)
    for path, e in entries.items():
        mine = {s for s in changed_syms if s.startswith(path + "::")}
        dist: dict[str, int] = {}
        for sid, d in _bfs(mine, head_rev, 3).items():
            p = sid.split("::", 1)[0]
            if p != path and d > 0:
                dist[p] = min(dist.get(p, 99), d)
        for p, d in _bfs({path}, file_importers, 2).items():
            if p != path and d > 0:
                dist[p] = min(dist.get(p, 99), d)
        e["blast"] = [{"path": p, "distance": d} for p, d in sorted(dist.items(), key=lambda kv: (kv[1], kv[0]))]

    # which starting points can reach the changed code
    by_id = {_sid(p, s.qual): s for p, i in head_files.items() for s in i.symbols}
    reach: list[dict] = []
    reach_by_file: dict[str, set[str]] = defaultdict(set)
    fwd = {sid: [c["id"] for c in s.callees] for sid, s in by_id.items()}
    for r in flow_roots:
        seen = _bfs({r["id"]}, fwd, 12)
        hit = sorted(s for s in changed_syms if s in seen)
        if hit:
            reach.append({"root": r["id"], "name": r["name"], "why": r["why"], "count": len(hit)})
            for s in hit:
                reach_by_file[s.split("::", 1)[0]].add(r["name"])
    reach.sort(key=lambda x: (-x["count"], x["name"]))

    # leftovers
    new_dead: list[dict] = []
    for sid in sorted(added_ids):
        path, qual = sid.split("::", 1)
        info, sym = head_files[path], by_id[sid]
        if (sym.kind == "module" or sym.name.startswith("__") or info.is_test or sym.entry or not info.has_calls
                or sid in head_rev):
            continue
        new_dead.append({"kind": "unused-new", "id": sid, "path": path, "qual": qual, "line": sym.line,
                         "why": "added, but nothing in the project calls it"})
    for sid in sorted(set(base_rev) & set(by_id)):
        path, qual = sid.split("::", 1)
        sym = by_id[sid]
        if sid in head_rev or sid in added_ids or sym.kind == "module" or sym.entry or head_files[path].is_test:
            continue
        if base_rev[sid]:
            new_dead.append({"kind": "orphaned", "id": sid, "path": path, "qual": qual, "line": sym.line,
                             "why": "used to be called, now nothing calls it"})
    unused_files = set()
    for path, info in sorted(head_files.items()):
        was = base_files.get(path)
        if info.possibly_unused and (path in entries) and (entries[path]["status"] == "A" or (was and not was.possibly_unused)):
            unused_files.add(path)
            new_dead.append({"kind": "unused-file", "id": path, "path": path, "qual": "", "line": 1,
                             "why": "no file imports it"})
    # a whole unused file already says it; don't repeat for each function inside
    new_dead = [d for d in new_dead if d["kind"] == "unused-file" or d["path"] not in unused_files]

    risks: list[dict] = []
    gone_files = {(e["oldPath"] or p) for p, e in entries.items() if e["status"] in ("D", "R")}
    for old_path, qual in removed:
        if old_path in gone_files:  # reported once, at file level, below
            continue
        sid = _sid(old_path, qual)
        users = sorted(u for u in base_rev.get(sid, {}) if u.split("::", 1)[0] in head_files
                       and u.split("::", 1)[0] not in entries)
        if users:
            risks.append({"kind": "removed-still-used", "what": sid, "users": users[:10],
                          "why": "removed, but code that was not changed still called it"})
    for path, e in entries.items():
        if e["status"] in ("D", "R"):
            old = e["oldPath"] or path
            users = sorted(p for p in (base_files[old].imported_by if old in base_files else [])
                           if p in head_files and p not in entries)
            if users:
                risks.append({"kind": "removed-still-used", "what": old, "users": users[:10],
                              "why": "removed or moved, but unchanged files imported it"})

    # copy-paste
    base_hash_set = {h for h, _ in base_hashes.values()}
    new_dups = []
    for g in find_duplicates(head_files, head_hashes):
        if any(m["id"] in changed_syms for m in g["members"]) and g["hash"] not in base_hash_set:
            new_dups.append(g)

    # reading order
    for path, e in entries.items():
        blast_n = len(e["blast"])
        reach_n = len(reach_by_file.get(path, ()))
        cdelta = max(0, e["complexityAfter"] - e["complexityBefore"])
        lines = e["added"] + e["deleted"]
        score = (10 * min(blast_n, 20) / 20 + 6 * min(reach_n, 3) / 3 + 6 * min(cdelta, 30) / 30
                 + 4 * min(lines, 300) / 300 + (3 if e["status"] == "A" and e["analyzed"] else 0)
                 + (2 if e["status"] == "D" else 0))
        why = []
        if blast_n:
            why.append(f"{_plural(blast_n, 'other file')} depend on it")
        if reach_n:
            why.append("runs from " + _list(sorted(reach_by_file[path]), 2, False))
        if cdelta >= 3:
            why.append(f"adds complexity (+{cdelta})")
        if e["status"] == "A":
            why.append("brand-new file")
        if e["status"] == "D":
            why.append("removed: check nothing still needs it")
        if lines >= 100:
            why.append(f"large change ({lines} lines)")
        if e["isTest"]:
            score *= 0.3
            why.append("test: read after the code it checks")
        if not why:
            why.append("small, self-contained change")
        e["score"], e["why"] = round(score, 2), why
        e["explain"] = _explain_change(e, blast_n)
    order = sorted(entries.values(), key=lambda e: (e["isTest"], -e["score"], e["path"]))

    summary = {
        "files": len(entries),
        "added": sum(1 for e in entries.values() if e["status"] == "A"),
        "deleted": sum(1 for e in entries.values() if e["status"] == "D"),
        "modified": sum(1 for e in entries.values() if e["status"] in ("M", "R")),
        "linesAdded": sum(e["added"] for e in entries.values()),
        "linesDeleted": sum(e["deleted"] for e in entries.values()),
        "symbolsAdded": len(added_ids), "symbolsModified": len(modified_ids), "symbolsRemoved": len(removed),
        "complexityDelta": sum(e["complexityAfter"] - e["complexityBefore"] for e in entries.values()),
        "impactedFiles": len({b["path"] for e in entries.values() for b in e["blast"]} - set(entries)),
        "newDead": len(new_dead), "newDuplicates": len(new_dups), "risks": len(risks),
    }
    return {
        "base": base_ref, "head": head_ref or "working tree", "summary": summary,
        "files": {e["path"]: e for e in entries.values()},
        "reviewOrder": [e["path"] for e in order],
        "reach": reach[:12], "newDead": new_dead, "newDuplicates": new_dups, "risks": risks,
    }


# ------------------------------------------------------------------ words


def _explain_change(e: dict, blast_n: int) -> dict:
    name = posixpath.basename(e["path"])
    s = e["symbols"]
    verb_k = {"A": "a brand-new room", "D": "a room that was knocked down", "R": "a room that was moved", "M": "a room that was changed"}[e["status"]]
    verb_p = {"A": "a new file", "D": "a deleted file", "R": "a renamed file", "M": "a modified file"}[e["status"]]
    kid = [f"`{name}` is {verb_k}. {_plural(e['added'], 'line')} {'was' if e['added'] == 1 else 'were'} added and {e['deleted']} taken away."]
    plain = [f"`{name}` is {verb_p}: +{e['added']} / -{e['deleted']} lines."]
    for key, kk, pp in (("added", "New helpers", "Added"), ("modified", "Changed helpers", "Modified"), ("removed", "Helpers that are gone", "Removed")):
        if s[key]:
            kid.append(f"{kk}: {_list(s[key], 4, False)}.")
            plain.append(f"{pp}: {_list(s[key], 4)}.")
    if blast_n:
        kid.append(f"If something goes wrong here, about {_plural(blast_n, 'other room')} could feel it.")
        plain.append(f"{_plural(blast_n, 'other file')} depend on this code, directly or indirectly.")
    elif e["status"] != "D":
        kid.append("Nobody else seems to depend on it, so it is low-risk.")
        plain.append("Nothing else appears to depend on it.")
    return {"kid": " ".join(kid), "plain": " ".join(plain)}


def story_steps(ch: dict) -> list[dict]:
    s = ch["summary"]
    order = [p for p in ch["reviewOrder"]][:3]
    top = [ch["files"][p] for p in order]
    kid = (f"Somebody has been rearranging the house! {_plural(s['files'], 'room')} {'was' if s['files'] == 1 else 'were'} touched "
           f"({s['added']} new, {s['modified']} changed, {s['deleted']} knocked down), with {s['linesAdded']} lines added and "
           f"{s['linesDeleted']} taken away. About {_plural(s['impactedFiles'], 'other room')} might notice.")
    plain = (f"{_plural(s['files'], 'file')} changed ({s['added']} added, {s['modified']} modified, {s['deleted']} deleted): "
             f"+{s['linesAdded']} / -{s['linesDeleted']} lines. {_plural(s['impactedFiles'], 'unchanged file')} depend on changed code.")
    steps = [{"title": {"kid": "What changed", "plain": f"Changes vs {ch['base']}"}, "text": {"kid": kid, "plain": plain}}]
    if top:
        steps.append({
            "title": {"kid": "Look at these rooms first", "plain": "Review these first"},
            "text": {
                "kid": "Start with the rooms where a mistake would hurt the most:\n" +
                       "\n".join(f"{i}. {t['path']} ({', '.join(t['why'][:2])})" for i, t in enumerate(top, 1)),
                "plain": "Suggested review order:\n" + "\n".join(f"{i}. {t['path']}: {'; '.join(t['why'][:3])}" for i, t in enumerate(top, 1)),
            },
            "path": top[0]["path"], "change": True,
        })
    checks_k, checks_p = [], []
    if ch["risks"]:
        checks_k.append(f"{_plural(len(ch['risks']), 'thing')} {'was' if len(ch['risks']) == 1 else 'were'} removed even though rooms that were not changed still use {'it' if len(ch['risks']) == 1 else 'them'}.")
        checks_p.append(f"{_plural(len(ch['risks']), 'removal')} may still be used by unchanged code.")
    if ch["newDead"]:
        checks_k.append(f"{_plural(len(ch['newDead']), 'new helper or room')} might be left lying around because nobody uses {'it' if len(ch['newDead']) == 1 else 'them'}.")
        checks_p.append(f"{len(ch['newDead'])} new or orphaned item(s) with no callers.")
    if ch["newDuplicates"]:
        checks_k.append(f"{_plural(len(ch['newDuplicates']), 'helper')} {'was' if len(ch['newDuplicates']) == 1 else 'were'} copy-pasted instead of shared.")
        checks_p.append(f"{len(ch['newDuplicates'])} new copy-pasted function(s).")
    if s["complexityDelta"] > 10:
        checks_k.append("The code got more tangled (more forks in the road).")
        checks_p.append(f"Overall complexity rose by {s['complexityDelta']}.")
    steps.append({
        "title": {"kid": "Things to double-check", "plain": "Things to double-check"},
        "text": {"kid": " ".join(checks_k) or "I did not spot anything suspicious, but it is still worth a look.",
                 "plain": " ".join(checks_p) or "No leftovers, risky removals or new duplicates detected."},
        "change": True,
    })
    return steps
