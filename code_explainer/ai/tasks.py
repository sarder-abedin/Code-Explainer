"""What to ask the model, and what to show it, for an "Explain with AI" button.

The browser only names a target (a file, a function, a step in a flow, a change). Everything
the model sees is built here from the analysis: the numbered code, exact facts from the call
graph, the code it connects to, and for changes the patch. The wording of the task is ours, so
a page can not steer the prompt, and the same target always yields the same text, which is what
lets answers be saved and reused (see cache.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .prompts import estimate_tokens

TEMPLATE_VERSION = 1
KINDS = ("file", "symbol", "flow", "change")
MIN_BUDGET_TOKENS = 60
SYMBOL_LINES = 120
FLOW_LINES = 60
CALLEE_LINES = 25
CALLEES = 3
FILE_FULL_LINES = 200
FILE_HEAD_LINES = 60
OUTLINE_MAX = 150
PATCH_CHARS = 10_000
CHANGE_SYMBOLS = 3
CHANGE_SYMBOL_LINES = 40
_CALL_WINDOW = 6
_MAX_FIELD = 500


class TaskError(ValueError):
    """The request does not name something we can explain."""


@dataclass
class Task:
    kind: str
    target: str                 # stable id: "path", "path::qual" or "path" for a change
    title: str
    message: str                # the user turn we send
    context: str                # what goes inside the fence
    sources: list = field(default_factory=list)

    @property
    def content(self) -> str:
        """Exactly what determines the answer; the cache keys on a hash of this."""
        return self.message + "\n\x00\n" + self.context


def _field(req: dict, name: str, required: bool = True) -> str:
    v = req.get(name)
    if v is None and not required:
        return ""
    if not isinstance(v, str) or not v or len(v) > _MAX_FIELD or "\x00" in v:
        raise TaskError(f"the request needs a valid {name}")
    return v


def _numbered(lines: list[str], start: int) -> str:
    return "\n".join(f"{n:>4}| {l}" for n, l in zip(range(start, start + len(lines)), lines))


class _Budget:
    """Collects parts of the context in priority order and stops at the token budget."""

    def __init__(self, tokens: int):
        self.limit, self.used = tokens, 0
        self.parts: list[tuple[int, int, str]] = []   # (reading order, insertion order, text)
        self.sources: list[dict] = []

    @property
    def room(self) -> int:
        return self.limit - self.used

    def text(self, block: str, order: int = 0) -> bool:
        """Insertion order is priority (earlier calls get the budget first); `order` is where it appears when read."""
        cost = estimate_tokens(block) + 1
        if cost > self.room:
            return False
        self.parts.append((order, len(self.parts), block))
        self.used += cost
        return True

    def code(self, path: str, start: int, lines: list[str], label: str, reason: str, note: str = "", more: int = 0,
             required: bool = False, order: int = 2) -> bool:
        """Add numbered code. `lines` is the slice starting at line `start`. The first (required) block is trimmed
        to fit; later ones are skipped when they do not fit."""
        if not lines:
            return False
        keep = len(lines)
        while keep and self._cost(path, start, lines[:keep], label, note, more if keep == len(lines) else 0) > self.room:
            keep = keep - 1 if keep <= 8 else int(keep * 0.8)
        if keep < len(lines) and not required:
            return False
        if keep == 0:
            return False
        shown = lines[:keep]
        tail = more if keep == len(lines) else (len(lines) - keep + more)
        self.text(self._block(path, start, shown, label, note, tail, cut=keep < len(lines)), order)
        self.sources.append({"path": path, "line": start, "end": start + keep - 1, "label": label, "reason": reason})
        return True

    @staticmethod
    def _block(path, start, shown, label, note, more, cut=False) -> str:
        end = start + len(shown) - 1
        head = f"### {_clean(path)}:{start}-{end} — {label}{(' ' + note) if note else ''}\n"
        body = _numbered(shown, start)
        if more:
            body += f"\n     … ({more} more lines{', cut to fit' if cut else ''})"
        return head + body

    def _cost(self, path, start, shown, label, note, more) -> int:
        return estimate_tokens(self._block(path, start, shown, label, note, more)) + 1

    def context(self) -> str:
        return "\n\n".join(text for _, _, text in sorted(self.parts))


def _clean(path: str) -> str:
    return "".join("?" if ord(c) < 32 or ord(c) == 127 else c for c in path)


class _Project:
    def __init__(self, data: dict):
        self.data = data
        self.files = {f["path"]: f for f in data["files"]}
        self.by_sid: dict[str, tuple[dict, dict]] = {}
        self.tests = (data.get("tests") or {}).get("files", {})
        self.findings = (data.get("hygiene") or {}).get("findings", [])
        self.callers: dict[str, list[str]] = {}
        for f in data["files"]:
            for s in f["symbols"]:
                self.by_sid[f"{f['path']}::{s['qual']}"] = (f, s)
        for sid, (f, s) in self.by_sid.items():
            for c in s.get("callees", []):
                if c["conf"] == "certain" and c["id"] != sid:
                    self.callers.setdefault(c["id"], []).append(sid)

    def lines(self, path: str) -> list[str]:
        return (self.files[path].get("source") or "").splitlines()

    def label(self, sid: str, with_path: bool = False) -> str:
        f, s = self.by_sid[sid]
        if s["kind"] == "module":
            return f"the top-level code of {_clean(f['path'])}"
        return f"{s['qual']} ({_clean(f['path'])})" if with_path else s["qual"]

    def facts(self, sid: str) -> str:
        f, s = self.by_sid[sid]
        calls = [self.label(c) for c in (c["id"] for c in s.get("callees", []) if c["conf"] == "certain")
                 if c in self.by_sid and self.by_sid[c][1]["kind"] != "module"][:6]
        callers = [self.label(c, True) for c in self.callers.get(sid, [])]
        line = f"- {_clean(f['path'])}::{s['qual']} (lines {s['line']}-{s['end_line']})"
        line += f"; calls: {', '.join(calls)}" if calls else "; calls nothing else in this project"
        if callers:
            line += f"; called by: {', '.join(callers[:6])}" + (f" and {len(callers) - 6} more" if len(callers) > 6 else "")
        else:
            line += "; no caller found in this project"
        if s["qual"] in self.tests.get(f["path"], {}).get("untested", ()):
            line += "; no test was seen reaching it"
        return line


FACTS_HEAD = "Facts from static analysis (exact, from the call graph):\n"


def _find_symbol(proj: _Project, path: str, name: str) -> str:
    sid = f"{path}::{name}"
    if sid not in proj.by_sid or proj.by_sid[sid][1]["kind"] == "module":
        raise TaskError(f"no symbol named {name} in {path}")
    return sid


def _symbol_code(proj: _Project, b: _Budget, sid: str, max_lines: int, reason: str, note: str = "", required: bool = True,
                 order: int = 2) -> None:
    f, s = proj.by_sid[sid]
    path, start = f["path"], s["line"]
    end = s["end_line"]
    if s["kind"] == "class":
        end = min(end, start + 11)
    total = end - start + 1
    shown_end = start + min(total, max_lines) - 1
    lines = proj.lines(path)[start - 1: shown_end]
    b.code(path, start, lines, s["qual"], reason, note=note, more=max(0, total - max_lines), required=required, order=order)


def _build_symbol(proj, b, path, name, kind="symbol", parent=None, call_line=0):
    sid = _find_symbol(proj, path, name)
    f, s = proj.by_sid[sid]
    _symbol_code(proj, b, sid, SYMBOL_LINES if kind == "symbol" else FLOW_LINES, "the code you asked about", order=1)
    b.text(FACTS_HEAD + proj.facts(sid), order=0)
    if s["kind"] == "class":
        outline = [f"- {m['qual']} (lines {m['line']}-{m['end_line']})" + (f": {m['doc']}" if m.get("doc") else "")
                   for m in f["symbols"] if m["qual"].startswith(s["qual"] + ".")]
        if outline:
            b.text("Methods of " + s["qual"] + ":\n" + "\n".join(outline[:40]), order=2)
    if parent:
        pf, ps = proj.by_sid[parent]
        lo = max(ps["line"], call_line - _CALL_WINDOW) if call_line else ps["line"]
        hi = min(ps["end_line"], call_line + _CALL_WINDOW) if call_line else min(ps["end_line"], ps["line"] + 2 * _CALL_WINDOW)
        who = proj.label(parent)
        b.code(pf["path"], lo, proj.lines(pf["path"])[lo - 1: hi], who if ps["kind"] != "module" else "top-level code",
               f"calls {s['qual']}", note=f"(the caller; it calls {s['qual']} at line {call_line})" if call_line else "(the caller)", order=3)
    done = 0
    for c in s.get("callees", []):
        if done >= CALLEES:
            break
        if c["conf"] == "certain" and c["id"] in proj.by_sid and proj.by_sid[c["id"]][1]["kind"] != "module" and c["id"] != sid:
            cf, cs = proj.by_sid[c["id"]]
            total = cs["end_line"] - cs["line"] + 1
            lines = proj.lines(cf["path"])[cs["line"] - 1: cs["line"] - 1 + min(total, CALLEE_LINES)]
            if b.code(cf["path"], cs["line"], lines, cs["qual"], f"called by {s['qual']}", note=f"(called by {s['qual']})",
                      more=max(0, total - CALLEE_LINES), order=4):
                done += 1
    return sid


def _build_file(proj, b, path):
    f = proj.files[path]
    syms = [s for s in f["symbols"] if s["kind"] != "module"]
    lines = proj.lines(path)
    whole = len(lines) <= FILE_FULL_LINES and b.code(path, 1, lines, "the whole file", "the file you asked about", required=True, order=2)
    if not whole:
        head = lines[:FILE_HEAD_LINES]
        b.code(path, 1, head, f"the first {len(head)} lines", "the file you asked about",
               note=f"(showing the first {FILE_HEAD_LINES} lines and an outline of the rest)" if len(lines) > len(head) else "",
               more=max(0, len(lines) - len(head)), required=True, order=2)
        outline = [f"- {s['qual']} (lines {s['line']}-{s['end_line']})" + (f": {s['doc']}" if s.get("doc") else "") for s in syms][:OUTLINE_MAX]
        if outline:
            b.text(f"Outline of {_clean(path)} (every function and class):\n" + "\n".join(outline), order=1)
    bits = []
    if f.get("imports"):
        bits.append("imports: " + ", ".join(_clean(p) for p in f["imports"][:12]))
    if f.get("importedBy"):
        bits.append("imported by: " + ", ".join(_clean(p) for p in f["importedBy"][:12]))
    for why in f.get("entry", [])[:2]:
        bits.append(f"Entry point: {why}")
    if f.get("isTest"):
        bits.append("this is a test file")
    t = proj.tests.get(path)
    if t:
        label = {"tested": "fully tested", "partial": "partly tested", "untested": "no test was seen reaching it",
                 "imported": "only imported by tests, no function called", "unknown": "not known for this language"}[t["state"]]
        bits.append(f"Tests (static estimate): {label}" + (f"; no test seen reaching: {', '.join(t['untested'][:8])}" if t["state"] == "partial" else ""))
    notes = [x for x in proj.findings if x["path"] == path and not x["test"]][:5]
    if notes:
        bits.append("Safety notes: " + "; ".join(f"{x['title']} (line {x['line']})" for x in notes))
    b.text(f"Facts about {_clean(path)} (exact, from static analysis):\n- {f.get('lines', 0)} lines, {len(syms)} functions/classes"
           + "".join(f"\n- {x}" for x in bits), order=0)


def _build_change(proj, b, path, ch):
    e = ch["files"].get(path)
    if e is None:
        raise TaskError(f"{path} is not part of the change being reviewed")
    sy = e["symbols"]
    head = [f"- status: {e['status']} (+{e['added']} / -{e['deleted']} lines)"]
    for key in ("added", "modified", "removed"):
        if sy[key]:
            head.append(f"- {key}: {', '.join(sy[key][:12])}")
    if e.get("untested"):
        head.append("- changed code that no test was seen reaching: " + ", ".join(e["untested"][:10]))
    if e.get("blast"):
        head.append("- other files that depend on it: " + ", ".join(_clean(x["path"]) for x in e["blast"][:10]))
    for d in ch.get("newDead", []):
        if d.get("path") == path:
            head.append(f"- possible leftover: {d.get('qual') or path} ({d['why']})")
    for r in ch.get("risks", []):
        if path in r.get("what", ""):
            head.append(f"- risk: {r['why']}")
    patch = e.get("patch", "")
    room = max(200, (b.room - 150) * 3)                    # characters of patch we can afford, keeping some room for the facts
    shown = patch[: min(PATCH_CHARS, room)]
    b.text(f"### patch for {_clean(path)}\n" + shown + ("\n… (patch cut)" if len(shown) < len(patch) else ""), order=1)
    b.text("Facts about this change (exact, from static analysis):\n" + "\n".join(head), order=0)
    if path in proj.files:
        shown = 0
        for qual in sy["added"] + sy["modified"]:
            sid = f"{path}::{qual}"
            if shown >= CHANGE_SYMBOLS or sid not in proj.by_sid:
                continue
            f2, s2 = proj.by_sid[sid]
            total = s2["end_line"] - s2["line"] + 1
            lines = proj.lines(path)[s2["line"] - 1: s2["line"] - 1 + min(total, CHANGE_SYMBOL_LINES)]
            if b.code(path, s2["line"], lines, qual, "changed in this review", note="(current code)", more=max(0, total - CHANGE_SYMBOL_LINES)):
                shown += 1


_STATUS_WORDS = {"A": "was added", "D": "was removed", "R": "was renamed or moved", "M": "was modified"}


def build_task(data: dict, req: object, budget_tokens: int) -> Task:
    if not isinstance(req, dict):
        raise TaskError("the request must name what to explain")
    kind = req.get("kind")
    if kind not in KINDS:
        raise TaskError(f"kind must be one of {', '.join(KINDS)}")
    path = _field(req, "path")
    if budget_tokens < MIN_BUDGET_TOKENS:
        raise TaskError("the context window is too small to explain anything; raise the context size in the AI settings")
    proj = _Project(data)
    if kind == "change":
        ch = data.get("changes")
        if not ch:
            raise TaskError("no change review is loaded (start serve or analyze with --base)")
    elif path not in proj.files:
        raise TaskError(f"{path} is not in this project")
    b = _Budget(budget_tokens)

    if kind == "symbol":
        name = _field(req, "symbol")
        sid = _build_symbol(proj, b, path, name)
        message = (f"Explain `{name}` ({_clean(path)}, lines {proj.by_sid[sid][1]['line']}-{proj.by_sid[sid][1]['end_line']}). "
                   "Say what it is for, how it works step by step, and when it runs. Mention anything surprising or risky. "
                   "Cite the code as path:line.")
        return Task(kind, sid, f"Explain `{name}`", message, b.context(), b.sources)

    if kind == "flow":
        name = _field(req, "symbol")
        parent = req.get("parent")
        sid = f"{path}::{name}"
        _find_symbol(proj, path, name)
        pid, call_line = None, 0
        if isinstance(parent, str) and parent in proj.by_sid and parent != sid:
            pid = parent
            call_line = next((c["line"] for c in proj.by_sid[pid][1].get("callees", []) if c["id"] == sid), 0)
        _build_symbol(proj, b, path, name, kind="flow", parent=pid, call_line=call_line)
        who = proj.label(pid) if pid else ""
        caller = who if pid and proj.by_sid[pid][1]["kind"] == "module" else f"`{who}`"      # "the top-level code of x.py" is not a name
        message = (f"Explain what `{name}` ({_clean(path)}) does at this point of the program's flow."
                   + (f" It was called by {caller}" + (f" at line {call_line}" if call_line else "") + "." if pid else "")
                   + " Say what it receives, what it does and what it hands back. Cite the code as path:line.")
        return Task(kind, f"{sid}<-{pid}" if pid else sid, f"Explain this step: `{name}`", message, b.context(), b.sources)

    if kind == "file":
        _build_file(proj, b, path)
        message = (f"Explain the file `{_clean(path)}`: what it is for, how its parts fit together, and how it connects to the rest "
                   "of the project. Cite the code as path:line.")
        return Task(kind, path, f"Explain file `{_clean(path)}`", message, b.context(), b.sources)

    _build_change(proj, b, path, data["changes"])
    status = data["changes"]["files"][path]["status"]
    message = (f"The file `{_clean(path)}` {_STATUS_WORDS.get(status, 'was changed')} in the change being reviewed. "
               "Explain what changed and why it might matter, what could break, and what to double-check. "
               "Cite the code as path:line.")
    return Task(kind, path, f"Explain this change: `{_clean(path)}`", message, b.context(), b.sources)
