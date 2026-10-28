"""Streaming HTTP for model servers, with real cancellation.

A reader thread owns the connection and feeds lines into a queue; the consumer polls the
queue, so it can notice a cancel request, an idle model, or a closed browser even while the
server is silent (model loading and prompt processing can take minutes). Cancelling shuts
the socket down from outside, which unblocks the reader at once and tells the server to
stop generating.
"""

from __future__ import annotations

import http.client
import queue
import socket
import threading
import time
import urllib.parse
from typing import Iterator

from .http import ProviderError, check_endpoint_url

_END = object()


class Cancelled(Exception):
    """The user asked to stop."""


class LineStream:
    def __init__(self, url: str, body: dict | None, headers: dict, cancel: threading.Event, *,
                 idle_timeout: float = 120.0, tick_seconds: float = 5.0, allow_remote: bool = False,
                 method: str = "POST"):
        self.url = check_endpoint_url(url, allow_remote)
        self.body, self.headers, self.cancel, self.method = body, headers, cancel, method
        self.idle_timeout, self.tick_seconds = idle_timeout, tick_seconds
        self._q: queue.Queue = queue.Queue()
        self._conn: http.client.HTTPConnection | None = None
        self._sock: socket.socket | None = None  # kept separately: http.client forgets conn.sock after the headers
        self._closed = threading.Event()

    # ------------------------------------------------------------ reader thread
    def _read(self) -> None:
        parts = urllib.parse.urlsplit(self.url)
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        try:
            if self.cancel.is_set():
                return
            conn = cls(parts.hostname, parts.port, timeout=self.idle_timeout)
            self._conn = conn
            data = None
            hdrs = {"Accept": "*/*", **self.headers}
            if self.body is not None:
                import json
                data = json.dumps(self.body).encode("utf-8")
                hdrs["Content-Type"] = "application/json"
            path = parts.path or "/"
            conn.request(self.method, path, body=data, headers=hdrs)
            self._sock = conn.sock
            if self.cancel.is_set():
                return
            resp = conn.getresponse()
            if resp.status >= 300:
                detail = ""
                try:
                    detail = resp.read(400).decode("utf-8", "replace").strip()
                    import json as _j
                    parsed = _j.loads(detail)
                    if isinstance(parsed, dict):
                        err = parsed.get("error")
                        detail = (err.get("message") if isinstance(err, dict) else err) or detail
                except Exception:
                    pass
                self._q.put(ProviderError("http", f"HTTP {resp.status}" + (f": {detail}" if detail else ""), resp.status))
                return
            while True:
                line = resp.readline()
                if not line:
                    break
                self._q.put(line)
        except Exception as e:
            if not (self._closed.is_set() or self.cancel.is_set()):
                self._q.put(_map(e))
        finally:
            self._q.put(_END)

    def _abort(self) -> None:
        self._closed.set()
        sock, conn = self._sock, self._conn
        if sock is None and conn is not None:
            sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)  # wakes the reader thread and stops the server generating
            except OSError:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------ consumer
    def lines(self) -> Iterator[bytes | None]:
        """Yields raw lines; yields None as a tick when nothing has arrived for tick_seconds."""
        thread = threading.Thread(target=self._read, name="ai-stream-reader", daemon=True)
        thread.start()
        last_data = last_tick = time.monotonic()
        try:
            while True:
                if self.cancel.is_set():
                    raise Cancelled()
                try:
                    item = self._q.get(timeout=0.1)
                except queue.Empty:
                    now = time.monotonic()
                    if now - last_data > self.idle_timeout:
                        raise ProviderError("timeout", f"the model sent nothing for {self.idle_timeout:.0f} seconds")
                    if now - last_tick >= self.tick_seconds:
                        last_tick = now
                        yield None
                    continue
                if item is _END:
                    if self.cancel.is_set():
                        raise Cancelled()
                    return
                if isinstance(item, Exception):
                    raise item
                last_data = last_tick = time.monotonic()
                yield item
        finally:
            self._abort()


def _map(e: Exception) -> ProviderError:
    if isinstance(e, ProviderError):
        return e
    if isinstance(e, (socket.timeout, TimeoutError)):
        return ProviderError("timeout", "the server did not answer in time")
    if isinstance(e, (ConnectionError, OSError, http.client.HTTPException)):
        return ProviderError("unreachable", f"cannot connect ({e})")
    return ProviderError("bad_response", str(e))
