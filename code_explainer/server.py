"""`serve` mode: the same report, kept live and with progress that survives reloads.

* The page is rendered once; the browser polls /api/version and re-fetches the data
  when files change, so edits (for example by a coding agent) show up without a refresh.
* "I understand this" marks and notes are stored outside the repository, in
  ~/.code-explainer/<repo id>/progress.json.
* Binds to 127.0.0.1 by default: the report contains the repository's source.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .ai import discovery as ai_discovery
from .ai import params as ai_params
from .ai import settings as ai_settings
from .core import analyze_repo
from .ingest import IGNORED_DIRS
from .paths import state_dir
from .report import render_html

MAX_BODY = 1_000_000
POLL_SECONDS = 2.0


def repo_id(root: Path) -> str:
    return hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:12]


def signature(root: Path) -> str:
    """Cheap fingerprint of the working tree: paths, sizes and mtimes."""
    h = hashlib.sha1()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            h.update(f"{full}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def clean_progress(raw: object) -> dict:
    """Accept only the shape we store; drop everything else."""
    out = {"understood": {}, "notes": {}, "bookmarks": []}
    if not isinstance(raw, dict):
        return out
    u = raw.get("understood")
    if isinstance(u, dict):
        out["understood"] = {str(k)[:500]: True for k, v in list(u.items())[:20000] if v}
    n = raw.get("notes")
    if isinstance(n, dict):
        out["notes"] = {str(k)[:500]: v[:20000] for k, v in list(n.items())[:5000] if isinstance(v, str) and v.strip()}
    b = raw.get("bookmarks")
    if isinstance(b, list):
        out["bookmarks"] = [str(x)[:500] for x in b[:2000] if isinstance(x, str)]
    return out


class Session:
    """Holds the latest analysis and re-runs it when the tree changes."""

    def __init__(self, root: Path, name: str, base: str | None, head: str | None):
        self.root, self.name, self.base, self.head = root, name, base, head
        self.lock = threading.Lock()
        self.version = 0
        self.error = ""
        self.progress_path = state_dir() / repo_id(root) / "progress.json"
        self._sig = ""
        self.data: dict = {}
        self.refresh()

    def refresh(self) -> None:
        sig = signature(self.root)
        try:
            data = analyze_repo(self.root, self.name, base=self.base, head=self.head)
            err = ""
        except Exception as e:  # keep serving the last good analysis
            data, err = None, f"{type(e).__name__}: {e}"
        with self.lock:
            self._sig = sig
            self.error = err
            if data is not None:
                self.data = data
                self.version += 1

    def changed(self) -> bool:
        return signature(self.root) != self._sig

    def watch(self, stop: threading.Event) -> None:
        while not stop.wait(POLL_SECONDS):
            if self.changed():
                stop.wait(0.5)  # let an in-progress write finish
                self.refresh()

    def load_progress(self) -> dict:
        try:
            return clean_progress(json.loads(self.progress_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return clean_progress(None)

    def save_progress(self, raw: object) -> dict:
        data = clean_progress(raw)
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.progress_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.progress_path)
        return data


def make_handler(session: Session, any_host: bool = False):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CodeExplainer"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _allowed(self) -> set[str]:
            port = self.server.server_port
            return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

        def _host_ok(self) -> bool:
            # Rejects DNS-rebinding: a hostile page cannot make us answer under its own name.
            return any_host or self.headers.get("Host", "") in self._allowed()

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            if not self._host_ok():
                return self._json({"error": "bad host"}, 403)
            path = self.path.split("?", 1)[0]
            if path == "/":
                with session.lock:
                    html = render_html(session.data, serve={"version": session.version, "id": repo_id(session.root)})
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path == "/api/version":
                return self._json({"version": session.version, "error": session.error})
            if path == "/api/data":
                with session.lock:
                    return self._json({"version": session.version, "data": session.data})
            if path == "/api/progress":
                return self._json(session.load_progress())
            if path == "/api/ai/settings":
                return self._json({"settings": ai_settings.load(), "schema": ai_params.schema()})
            if path == "/api/ai/providers":
                st = ai_settings.load()
                return self._json({"providers": ai_discovery.discover(st), "selected": st["selected"]})
            self._json({"error": "not found"}, 404)

        def _read_json_post(self):
            """Shared checks for every POST. Returns (ok, parsed); on failure the error is already sent."""
            origin = self.headers.get("Origin")
            if origin and not any_host and origin.split("://", 1)[-1] not in self._allowed():
                self._json({"error": "bad origin"}, 403)
                return False, None
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json({"error": "expected JSON"}, 415)
                return False, None
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                self._json({"error": "bad length"}, 413)
                return False, None
            try:
                return True, json.loads(self.rfile.read(length) or b"null")
            except ValueError:
                self._json({"error": "bad json"}, 400)
                return False, None

        def do_POST(self):
            if not self._host_ok():
                return self._json({"error": "bad host"}, 403)
            path = self.path.split("?", 1)[0]
            if path not in ("/api/progress", "/api/ai/settings"):
                return self._json({"error": "not found"}, 404)
            ok, raw = self._read_json_post()
            if not ok:
                return
            if path == "/api/progress":
                return self._json(session.save_progress(raw))
            self._json({"settings": ai_settings.update(raw), "schema": ai_params.schema()})

    return Handler


def serve(root: Path, name: str, host: str = "127.0.0.1", port: int = 8765, base: str | None = None,
          head: str | None = None, watch: bool = True, on_ready=None) -> None:
    session = Session(root, name, base, head)
    loopback = host in ("127.0.0.1", "localhost", "::1")
    httpd = ThreadingHTTPServer((host, port), make_handler(session, any_host=not loopback))
    stop = threading.Event()
    if watch:
        threading.Thread(target=session.watch, args=(stop,), daemon=True).start()
    if on_ready:
        on_ready(httpd, session)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
