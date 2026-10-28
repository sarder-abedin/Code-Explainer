"""Copy-pasted function detection.

Two functions are duplicates when everything after their first line (the
signature) is identical once blank lines, full-line comments and indentation are
ignored. Renamed variables defeat it on purpose: this finds literal copy-paste,
which is what coding agents tend to produce, with very few false positives.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from .model import FileInfo

MIN_LINES = 6
_COMMENT = re.compile(r"^\s*(#|//|\*|/\*)")


def body_hash(info: FileInfo, line: int, end_line: int) -> tuple[str, int] | None:
    """(hash, meaningful line count) of a symbol's body, or None when too short or unavailable."""
    if not info.source:
        return None
    lines = info.source.splitlines()[line - 1:end_line]
    body = [ln.strip() for ln in lines[1:] if ln.strip() and not _COMMENT.match(ln)]
    if len(body) < MIN_LINES:
        return None
    return hashlib.sha1("\n".join(body).encode("utf-8", "replace")).hexdigest(), len(body)


def symbol_hashes(files: dict[str, FileInfo]) -> dict[str, tuple[str, int]]:
    out = {}
    for path, info in files.items():
        for sym in info.symbols:
            if sym.kind in ("function", "method"):
                h = body_hash(info, sym.line, sym.end_line)
                if h:
                    out[f"{path}::{sym.qual}"] = h
    return out


def find_duplicates(files: dict[str, FileInfo], hashes: dict[str, tuple[str, int]] | None = None) -> list[dict]:
    hashes = hashes if hashes is not None else symbol_hashes(files)
    groups: dict[str, list[str]] = defaultdict(list)
    for sid, (h, _n) in hashes.items():
        groups[h].append(sid)
    out = []
    for h, ids in groups.items():
        if len(ids) < 2:
            continue
        lines = hashes[ids[0]][1]
        members = []
        for sid in sorted(ids):
            path, qual = sid.split("::", 1)
            sym = next(s for s in files[path].symbols if s.qual == qual)
            members.append({"id": sid, "path": path, "qual": qual, "line": sym.line})
        out.append({"hash": h, "lines": lines, "members": members})
    out.sort(key=lambda g: (-(g["lines"] * (len(g["members"]) - 1)), g["hash"]))
    return out
