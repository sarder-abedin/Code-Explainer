"""Saved AI explanations, so coming back to the same code is instant (and free).

* The key covers everything that changes the answer: what kind of explanation, which code
  (a hash of the exact text sent), which model, every generation setting, the reading level,
  and the prompt template version. Edit the code, switch model or move a slider and you get a
  fresh answer; nothing stale is ever shown.
* Only the answer, its sources and the settings are stored. The prompt (which contains source
  code) is never written to disk.
* One small JSON file per entry under ~/.code-explainer/ai-cache/<repo id>/, written atomically
  with owner-only permissions. A corrupt or oversized file is simply a miss. Keys are validated
  as SHA-256 hex before they touch the filesystem, so a key can never become a path.
* The oldest entries are evicted beyond `max_entries`; reading an entry keeps it alive.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

_HEX = re.compile(r"^[0-9a-f]{64}$")
# The generation settings that change what the model writes. Anything else is ignored, so an
# unrelated field can never fragment the cache.
KEY_PARAMS = ("temperature", "top_p", "top_k", "min_p", "max_tokens", "repeat_penalty", "seed", "level", "length", "instruction")
_FIELDS = ("text", "stats", "model", "provider", "sources", "options", "level", "created")


class ExplainCache:
    def __init__(self, directory: Path | str, max_entries: int = 500, max_entry_bytes: int = 200_000):
        self.dir = Path(directory)
        self.max_entries, self.max_entry_bytes = max_entries, max_entry_bytes

    # ------------------------------------------------------------ keys
    @staticmethod
    def make_key(kind: str, target: str, content: str, provider: str, model: str, params: dict, version: int) -> str:
        """SHA-256 over a length-prefixed encoding, so text can't be shifted between fields to collide."""
        picked = {k: params.get(k) for k in KEY_PARAMS}
        parts = [str(version), kind, target, hashlib.sha256(content.encode("utf-8", "replace")).hexdigest(),
                 provider, model, json.dumps(picked, sort_keys=True, separators=(",", ":"))]
        blob = b"".join(len(p.encode()).to_bytes(4, "big") + p.encode("utf-8", "replace") for p in parts)
        return hashlib.sha256(blob).hexdigest()

    def _path(self, key) -> Path | None:
        if not isinstance(key, str) or not _HEX.match(key):
            return None
        return self.dir / key[:2] / f"{key}.json"

    # ------------------------------------------------------------ storage
    def get(self, key) -> dict | None:
        path = self._path(key)
        if path is None:
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            entry = json.loads(raw)
            if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                raise ValueError("wrong shape")
        except ValueError:
            try:
                path.unlink()
            except OSError:
                pass
            return None
        try:
            os.utime(path)  # reading keeps an entry alive
        except OSError:
            pass
        return {k: entry[k] for k in _FIELDS if k in entry}

    def put(self, key, entry: dict) -> bool:
        path = self._path(key)
        if path is None or not isinstance(entry.get("text"), str):
            return False
        clean = {k: entry[k] for k in _FIELDS if k in entry}
        clean["created"] = time.time()
        data = json.dumps(clean, ensure_ascii=False)
        if len(data.encode("utf-8")) > self.max_entry_bytes:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(data)
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            return False
        self._evict()
        return True

    def _files(self) -> list[Path]:
        try:
            return [p for p in self.dir.glob("*/*.json") if _HEX.match(p.stem)]
        except OSError:
            return []

    def _evict(self) -> None:
        files = self._files()
        if len(files) <= self.max_entries:
            return
        def age(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0
        for p in sorted(files, key=age)[: len(files) - self.max_entries]:
            try:
                p.unlink()
            except OSError:
                pass

    def stats(self) -> dict:
        n = size = 0
        for p in self._files():
            try:
                size += p.stat().st_size
                n += 1
            except OSError:
                pass
        return {"entries": n, "bytes": size}

    def clear(self) -> int:
        removed = 0
        for p in self._files():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        return removed
