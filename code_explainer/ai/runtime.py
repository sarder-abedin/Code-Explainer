"""Everything AI that a serving session owns: the backend lifecycle and the chat service."""

from __future__ import annotations

import threading

from .chat import ChatService
from .lifecycle import Lifecycle
from .retrieval import Retriever


class AIRuntime:
    def __init__(self, get_data=None, **lifecycle_kwargs):
        """`get_data()` returns (version, analysis data); without it answers are not grounded in the repository."""
        self.lifecycle = Lifecycle(**lifecycle_kwargs)
        self.chat = ChatService(self.lifecycle, retriever=Retriever(get_data) if get_data else None)
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None

    def start_watcher(self, every: float = 15.0) -> None:
        if self._watcher is None:
            self._watcher = threading.Thread(target=self.lifecycle.watch, args=(self._stop, every),
                                             name="ai-idle-watch", daemon=True)
            self._watcher.start()

    def status(self) -> dict:
        return {**self.lifecycle.status(), "activeIds": sorted(self.chat.active)}

    def _cancel_answers(self) -> None:
        for rid in list(self.chat.active):
            self.chat.cancel(rid)

    def start(self, provider: str, endpoint: str, model: str) -> dict:
        """Start (or switch) the backend. An answer in progress is cancelled if the target changes."""
        current = self.lifecycle.status()
        if current["state"] in ("starting", "loading", "ready") and (current["provider"], current["model"]) != (provider, model):
            self._cancel_answers()
        return self.lifecycle.start(provider, endpoint, model)

    def stop(self) -> dict:
        """Cancel any answer in progress, then unload and stop whatever this session started."""
        self._cancel_answers()
        return self.lifecycle.stop()

    def shutdown(self) -> None:
        self._stop.set()
        self.stop()
