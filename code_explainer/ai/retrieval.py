"""Find the code that answers a question, so the model can cite it instead of guessing.

No embeddings and no model: a BM25 keyword index over the things the analysis already knows
(symbols with their docstrings and generated explanations, files, and chunks of documentation),
plus three signals plain keyword search lacks:

* exact mentions: a symbol or file named in the question wins;
* the open file: vague questions ("explain this") are about it;
* the call graph: facts about who calls what are exact, so they are handed over as facts, and
  the callers/callees of the best matches are pulled in as extra candidates.

Everything the repository contains is untrusted text. Snippets are labelled with a header we
build, and the whole block is fenced by the caller (see prompts.build_system).
"""

from __future__ import annotations

import math
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field

from .prompts import estimate_tokens

MAX_SNIPPET_LINES = 60
KEEP_LINES = 50
CHUNK_LINES = 40
MAX_CHUNKS = 600
TOP_HITS = 8
EXPAND_FROM = 3
EXACT_BONUS = 8.0
FOCUS_BONUS = 3.0
FOCUS_VAGUE_BONUS = 12.0
FOCUS_SYMBOL_BONUS = 20.0
HISTORY_WEIGHT = 0.4
K1, B = 1.2, 0.75

_STOP = set("""a an and are as at be but by can could did do does for from had has have how i if in into is it its me my of on or
our please should so than that the their them then there these they this to us was we were what when where which who why will with
would you your about show tell explain describe give work works working used use using code function file class method thing
""".split())
_DEICTIC = {"this", "it", "here", "above", "current", "open", "that", "these"}
_WORD = re.compile(r"[A-Za-z]+|\d+")
_PIECE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _stem(w: str) -> str:
    """A deliberately light stemmer: plurals, -ing, -ed and a trailing e fold together (price/prices,
    calculate/calculated/calculating all become the same token). Numbers are left alone."""
    if w.isdigit():
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) > 3:
        w = w[:-1]
    for suffix in ("ing", "ed"):
        if w.endswith(suffix) and len(w) > len(suffix) + 2:
            w = w[: -len(suffix)]
            break
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


def tokenize(text: str) -> list[str]:
    """Lower-case words, with snake_case and camelCase split, filler words dropped and plurals folded."""
    out = []
    for word in _WORD.findall(text or ""):
        for piece in _PIECE.findall(word):
            w = piece.lower()
            if (len(w) >= 2 or w.isdigit()) and w not in _STOP:
                out.append(_stem(w))
    return out


def _clean_path(path: str) -> str:
    return _CTRL.sub("?", path)


@dataclass
class Retrieval:
    context: str = ""
    sources: list[dict] = field(default_factory=list)
    info: dict = field(default_factory=dict)


@dataclass
class _Doc:
    id: int
    kind: str                 # symbol | file | chunk
    path: str
    label: str                # qualified symbol name, or the file/chunk title
    line: int
    end: int
    tf: dict
    length: float
    is_test: bool
    importance: float = 0.0   # how much a symbol matters in its file: size, being public, branching
    is_class: bool = False
    sid: str = ""             # "path::qual" for symbols
    names: tuple = ()         # identifier names that count as an exact mention
    vocab: frozenset = frozenset()


class _Index:
    def __init__(self, data: dict):
        self.docs: list[_Doc] = []
        self.files = {f["path"]: f for f in data["files"]}
        self.lines: dict[str, list[str]] = {}
        self.by_sid: dict[str, _Doc] = {}
        self.callers: dict[str, list[str]] = defaultdict(list)
        self.callees: dict[str, list[str]] = {}
        self.file_docs: dict[str, list[_Doc]] = defaultdict(list)
        self.df: dict[str, int] = defaultdict(int)
        self._build(data)

    # --- building --------------------------------------------------------------
    def _src(self, path: str) -> list[str]:
        if path not in self.lines:
            self.lines[path] = (self.files[path].get("source") or "").splitlines()
        return self.lines[path]

    @staticmethod
    def _bag(parts: list[tuple[str, float]]) -> dict:
        tf: dict[str, float] = defaultdict(float)
        for text, weight in parts:
            for t in tokenize(text):
                tf[t] += weight
        return tf

    def _add(self, kind, path, label, line, end, parts, is_test, sid="", names=(), importance=0.0, is_class=False) -> _Doc:
        tf = self._bag(parts)
        doc = _Doc(len(self.docs), kind, path, label, line, end, tf, sum(tf.values()) or 1.0, is_test, importance, is_class,
                   sid, tuple(names), frozenset(tf))
        self.docs.append(doc)
        self.file_docs[path].append(doc)
        for t in tf:
            self.df[t] += 1
        return doc

    def _build(self, data: dict) -> None:
        for f in data["files"]:
            path, is_test = f["path"], bool(f.get("isTest"))
            expl = (f.get("explain") or {}).get("plain", "")
            base = path.rsplit("/", 1)[-1]
            sym_names = [s["name"] for s in f["symbols"] if s["kind"] != "module"]
            self._add("file", path, base, 1, max(1, min(f.get("lines", 1), 30)),
                      [(path, 2), (f.get("doc", ""), 2), (expl, 1), (" ".join(sym_names), 2)], is_test, names=(base, path))
            for s in f["symbols"]:
                if s["kind"] == "module":
                    continue
                end = s["end_line"] if s["kind"] != "class" else min(s["end_line"], s["line"] + 11)
                body = "\n".join(self._src(path)[s["line"] - 1: min(s["end_line"], s["line"] + 80)])[:4000]
                sid = f"{path}::{s['qual']}"
                importance = (min(s["end_line"] - s["line"] + 1, 60) / 60 * 4 + (0 if s["name"].startswith("_") else 1.5)
                              + min(s.get("complexity", 1), 10) / 10 * 2)
                doc = self._add("symbol", path, s["qual"], s["line"], end,
                                [(s["name"], 4), (s["qual"], 2), (path, 1), (s.get("doc", ""), 2),
                                 ((s.get("explain") or {}).get("plain", ""), 1), (body, 1)], is_test, sid,
                                names=(s["name"], s["qual"]), importance=importance, is_class=s["kind"] == "class")
                self.by_sid[sid] = doc
                self.callees[sid] = [c["id"] for c in s.get("callees", []) if c["conf"] == "certain"]
            if not f["symbols"] and f.get("source"):
                self._chunks(f, is_test)
        for sid, callees in self.callees.items():
            for c in callees:
                if c != sid:
                    self.callers[c].append(sid)

    def _chunks(self, f: dict, is_test: bool) -> None:
        if sum(1 for d in self.docs if d.kind == "chunk") >= MAX_CHUNKS:
            return
        lines = (f.get("source") or "").splitlines()[:2000]
        start = 0
        while start < len(lines):
            end = min(start + CHUNK_LINES, len(lines))
            while end < len(lines) and lines[end - 1].strip():      # prefer to end at a blank line
                end += 1
                if end - start > CHUNK_LINES + 20:
                    break
            text = "\n".join(lines[start:end])
            heading = next((l.strip("# ").strip() for l in lines[start:end] if l.strip()), "")
            self._add("chunk", f["path"], heading[:60] or f["path"], start + 1, end,
                      [(f["path"], 1), (heading, 3), (text[:3000], 1)], is_test, names=(f["path"].rsplit("/", 1)[-1], f["path"]))
            start = end

    # --- scoring ---------------------------------------------------------------
    def bm25(self, qweights: dict[str, float]) -> dict[int, float]:
        n = len(self.docs)
        avg = sum(d.length for d in self.docs) / n if n else 1.0
        scores: dict[int, float] = {}
        for term, qw in qweights.items():
            df = self.df.get(term)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for d in self.docs:
                tf = d.tf.get(term)
                if tf:
                    s = idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * d.length / avg))
                    scores[d.id] = scores.get(d.id, 0.0) + qw * s
        return scores


class Retriever:
    """`get_data()` returns (version, analysis data). The index is rebuilt only when the version changes."""

    def __init__(self, get_data):
        self._get_data = get_data
        self._lock = threading.Lock()
        self._version = None
        self._index: _Index | None = None

    def _index_for(self) -> _Index:
        version, data = self._get_data()
        with self._lock:
            if self._index is None or version != self._version:
                self._index, self._version = _Index(data), version
            return self._index

    def retrieve(self, question: str, *, history_questions=(), focus: dict | None = None, budget_tokens: int = 2000) -> Retrieval:
        idx = self._index_for()
        info = {"matched": 0, "used": 0, "tokens": 0, "budget": max(0, int(budget_tokens))}
        if budget_tokens < 60 or not idx.docs:
            return Retrieval("", [], info)
        qtok = tokenize(question)
        qweights: dict[str, float] = defaultdict(float)
        for t in qtok:
            qweights[t] += 1.0
        for prev in list(history_questions)[-2:]:
            for t in tokenize(prev):
                qweights.setdefault(t, 0.0)
                qweights[t] = max(qweights[t], HISTORY_WEIGHT) if t not in qtok else qweights[t]
        scores = idx.bm25(qweights)
        why: dict[int, str] = {}
        matched_terms = {i: [t for t in qweights if t in idx.docs[i].vocab] for i in scores}
        for i in scores:
            why[i] = "matches " + ", ".join(sorted(matched_terms[i], key=lambda t: -qweights[t])[:3])

        asked = set(_IDENT.findall(question))
        q_lower = question.lower()
        for d in idx.docs:
            hit = next((nm for nm in d.names if nm and (nm in asked or (("/" in nm or "." in nm) and nm.lower() in q_lower))), None)
            if hit:
                bonus = EXACT_BONUS * (0.75 if d.kind != "symbol" else 0.6 if d.is_class else 1.0)
                scores[d.id] = scores.get(d.id, 0.0) + bonus
                why[d.id] = "named in your question"
                if d.kind in ("file", "chunk"):  # naming a file makes its contents relevant
                    for sib in idx.file_docs[d.path]:
                        if sib.kind == "symbol":
                            scores[sib.id] = scores.get(sib.id, 0.0) + 3.0
                            why.setdefault(sib.id, "in a file named in your question")
                elif d.is_class:  # a class is explained by its methods
                    for sib in idx.file_docs[d.path]:
                        if sib.kind == "symbol" and sib.label.startswith(d.label + "."):
                            scores[sib.id] = scores.get(sib.id, 0.0) + 2.5
                            why.setdefault(sib.id, f"a method of {d.label}")

        wants_tests = "test" in q_lower
        if focus and focus.get("path") in idx.files:
            fpath, fsym = focus["path"], focus.get("symbol")
            vague = len(qtok) <= 6 and bool(_DEICTIC & set(re.findall(r"[a-z]+", q_lower)))
            for d in idx.file_docs[fpath]:
                bonus = (FOCUS_VAGUE_BONUS if vague else FOCUS_BONUS) + d.importance * (1.0 if vague else 0.3)
                if fsym and d.kind == "symbol" and fsym in (d.label, d.label.rsplit(".", 1)[-1]):
                    bonus = FOCUS_SYMBOL_BONUS
                scores[d.id] = scores.get(d.id, 0.0) + bonus
                why[d.id] = "the file you have open" if d.kind != "symbol" or not fsym else why.get(d.id, "the file you have open")
                if d.kind == "symbol" and bonus == FOCUS_SYMBOL_BONUS:
                    why[d.id] = "the code you have open"

        for i in list(scores):
            if idx.docs[i].is_test and not wants_tests:
                scores[i] *= 0.5
        matched = [i for i, s in scores.items() if s > 0]
        info["matched"] = len(matched)
        if not matched:
            return Retrieval("", [], info)
        ranked = sorted(matched, key=lambda i: (-scores[i], idx.docs[i].path, idx.docs[i].line, idx.docs[i].label))
        top = ranked[:TOP_HITS]

        # pull in neighbours of the best symbol matches through the call graph
        extra: dict[int, tuple[float, str]] = {}
        for i in top[:EXPAND_FROM]:
            d = idx.docs[i]
            if d.kind != "symbol":
                continue
            for callee in idx.callees.get(d.sid, [])[:3]:
                c = idx.by_sid.get(callee)
                if c and c.id not in top:
                    extra[c.id] = (scores[i] * 0.35, f"called by {d.label}")
            for caller in idx.callers.get(d.sid, [])[:3]:
                c = idx.by_sid.get(caller)
                if c and c.id not in top and c.id not in extra:
                    extra[c.id] = (scores[i] * 0.35, f"calls {d.label}")
        order = [(scores[i], i, why[i]) for i in top] + [(s, i, r) for i, (s, r) in extra.items()]
        # a file-level hit is better shown through its most important symbols
        order = self._expand_files(idx, order)
        order.sort(key=lambda x: (-x[0], idx.docs[x[1]].path, idx.docs[x[1]].line))
        return self._assemble(idx, order, budget_tokens, info)

    @staticmethod
    def _expand_files(idx: _Index, order):
        out, seen = [], set()
        for score, i, reason in order:
            d = idx.docs[i]
            if d.kind == "file":
                syms = sorted((s for s in idx.file_docs[d.path] if s.kind == "symbol" and "." not in s.label),
                              key=lambda s: (-(s.end - s.line), s.line))[:3]
                for s in syms:
                    if s.id not in seen:
                        seen.add(s.id)
                        out.append((score * 0.8, s.id, reason))
                if not syms and i not in seen:
                    seen.add(i)
                    out.append((score, i, reason))
            elif i not in seen:
                seen.add(i)
                out.append((score, i, reason))
        return out

    def _assemble(self, idx: _Index, order, budget: int, info: dict) -> Retrieval:
        # 1. merge overlapping or touching ranges inside one file, keeping the best reason
        picked: list[dict] = []
        for score, i, reason in order:
            d = idx.docs[i]
            end = min(d.end, d.line + KEEP_LINES - 1) if d.end - d.line + 1 > MAX_SNIPPET_LINES else d.end
            cut = d.end - end
            for p in picked:
                if p["path"] == d.path and d.line <= p["end"] + 2 and p["line"] <= end + 2:
                    p["line"], p["end"] = min(p["line"], d.line), max(p["end"], end)
                    p["labels"].append(d.label)
                    break
            else:
                picked.append({"path": d.path, "line": d.line, "end": end, "labels": [d.label], "reason": reason, "cut": cut, "sid": d.sid})
        # 2. facts about the strongest symbol matches
        facts: list[str] = []
        for score, i, _ in order[:5]:
            d = idx.docs[i]
            if d.kind != "symbol":
                continue
            calls = [idx.by_sid[c].label for c in idx.callees.get(d.sid, []) if c in idx.by_sid][:6]
            callers = [f"{idx.by_sid[c].label} ({_clean_path(idx.by_sid[c].path)})" for c in idx.callers.get(d.sid, []) if c in idx.by_sid]
            line = f"- {_clean_path(d.path)}::{d.label} (lines {d.line}-{d.end})"
            line += f"; calls: {', '.join(calls)}" if calls else "; calls nothing else in this project"
            line += (f"; called by: {', '.join(callers[:6])}" + (f" and {len(callers) - 6} more" if len(callers) > 6 else "")) if callers \
                else "; no caller found in this project"
            facts.append(line)
        parts: list[str] = []
        used = 0
        if facts:
            block = "Facts from static analysis (exact, from the call graph):\n" + "\n".join(facts) + "\n"
            cost = estimate_tokens(block)
            if cost <= budget * 0.3:
                parts.append(block)
                used += cost
        sources: list[dict] = []
        for p in picked:
            lines = idx._src(p["path"])
            text_lines = lines[p["line"] - 1: p["end"]]
            if not text_lines:
                continue
            header = f"### {_clean_path(p['path'])}:{p['line']}-{p['end']} — {', '.join(dict.fromkeys(p['labels']))}\n"
            body = "\n".join(f"{n:>4}| {l}" for n, l in zip(range(p["line"], p["end"] + 1), text_lines))
            if p["cut"] > 0:
                body += f"\n     … ({p['cut']} more lines)"
            cost = estimate_tokens(header + body) + 1
            if used + cost > budget:
                room = (budget - used) * 3 - len(header) - 40          # characters we can still spend
                if room < 120 or sources:
                    continue                                              # keep whole snippets; only the first may be trimmed
                keep = max(1, room // 40)
                text_lines = text_lines[:keep]
                p["end"] = p["line"] + len(text_lines) - 1
                header = f"### {_clean_path(p['path'])}:{p['line']}-{p['end']} — {', '.join(dict.fromkeys(p['labels']))}\n"
                body = "\n".join(f"{n:>4}| {l}" for n, l in zip(range(p["line"], p["end"] + 1), text_lines)) + "\n     … (cut to fit)"
                cost = estimate_tokens(header + body) + 1
                if used + cost > budget:
                    continue
            parts.append(header + body + "\n")
            used += cost
            sources.append({"path": p["path"], "line": p["line"], "end": p["end"], "label": p["labels"][0],
                            "labels": list(dict.fromkeys(p["labels"])), "reason": p["reason"]})
        context = "\n".join(parts).strip("\n")
        while context and estimate_tokens(context) > budget and len(parts) > 1:   # joins add a few characters; trim from the end
            parts.pop()
            if sources and len(parts) < len(sources) + (1 if facts and parts and parts[0].startswith("Facts") else 0):
                sources.pop()
            context = "\n".join(parts).strip("\n")
        if not sources:
            context = ""
        info.update(used=len(sources), tokens=estimate_tokens(context) if context else 0)
        return Retrieval(context, sources, info)
