"""One safe entry point for a chat turn: validate, build the prompt on the server, stream the
model's answer, and always release everything (the busy slot, the connection) however it ends.

The browser supplies only user/assistant turns, optional parameter overrides and optional
repository context. The model, the endpoint and the system prompt are never taken from it.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Iterator

from . import discovery
from . import settings as ai_settings
from .http import ProviderError
from .lifecycle import Lifecycle
from .params import GenParams, clean_params, to_provider_options
from .prompts import build_system, clean_messages, estimate_tokens, fit_messages
from .stream import Cancelled

MAX_CONTEXT_CHARS = 60_000
MAX_RETRIEVAL_TOKENS = 12_000
MAX_RETRIEVAL_SHARE = 0.6   # of the context window; the rest is for the conversation and the answer
_SAFETY_MARGIN_TOKENS = 64
_HINTS = {
    "not_ready": "Press “Start AI Explainer” first.",
    "busy": "Wait for the current answer to finish, or press Stop.",
    "unreachable": "The model server stopped answering. Press Start AI Explainer to try again.",
    "timeout": "The model took too long. Try a smaller model, a shorter question, or a smaller context size.",
}


def clean_focus(raw: object) -> dict | None:
    """The open file (and optionally a symbol in it), as sent by the browser. Anything odd is ignored."""
    if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
        return None
    path = raw["path"]
    if not path or len(path) > 500 or "\x00" in path:
        return None
    sym = raw.get("symbol")
    return {"path": path, "symbol": sym if isinstance(sym, str) and 0 < len(sym) <= 200 else None}


def _error(kind: str, message: str, hint: str = "") -> dict:
    return {"type": "error", "kind": kind, "message": message, "hint": hint or _HINTS.get(kind, "")}


class ChatService:
    def __init__(self, lifecycle: Lifecycle, load_settings=ai_settings.load, idle_timeout: float = 120.0,
                 tick_seconds: float = 5.0, retriever=None):
        self.lifecycle, self._load_settings, self.retriever = lifecycle, load_settings, retriever
        self.idle_timeout, self.tick_seconds = idle_timeout, tick_seconds
        self._slot = threading.Semaphore(1)
        self._lock = threading.Lock()
        self.active: dict[str, threading.Event] = {}

    def _retrieve(self, ground: bool, messages: list[dict], focus, params: GenParams) -> tuple[str, list, dict]:
        """Relevant code for the newest question, sized to what is left of the context window."""
        if not ground or self.retriever is None:
            return "", [], {"enabled": False}
        history = [m["content"] for m in messages[:-1] if m["role"] == "user"]
        used = estimate_tokens(build_system(params)) + sum(estimate_tokens(m["content"]) + 4 for m in messages)
        free = params.context_size - params.max_tokens - _SAFETY_MARGIN_TOKENS - used
        budget = int(min(max(free, 0), params.context_size * MAX_RETRIEVAL_SHARE, MAX_RETRIEVAL_TOKENS))
        try:
            r = self.retriever.retrieve(messages[-1]["content"], history_questions=history, focus=focus, budget_tokens=budget)
        except Exception as e:  # a retrieval bug must never stop the answer
            return "", [], {"enabled": True, "error": type(e).__name__}
        return r.context, r.sources, {"enabled": True, **r.info}

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            ev = self.active.get(request_id)
        if ev is None:
            return False
        ev.set()
        return True

    def stream(self, request: object, cancel: threading.Event | None = None) -> Iterator[dict]:
        req = request if isinstance(request, dict) else {}
        status = self.lifecycle.status()
        if status["state"] != "ready":
            yield _error("not_ready", "The AI Explainer is not running.")
            return
        settings = self._load_settings()
        try:
            messages = clean_messages(req.get("messages"))
        except ValueError as e:
            yield _error("bad_request", str(e))
            return
        params = clean_params(req.get("params"), GenParams(**settings["params"]))
        context = req.get("context")
        context = context.replace("\x00", "")[:MAX_CONTEXT_CHARS] if isinstance(context, str) else ""
        if not self._slot.acquire(blocking=False):
            yield _error("busy", "Another answer is still being written.")
            return

        rid = secrets.token_hex(8)
        cancel = cancel or threading.Event()
        with self._lock:
            self.active[rid] = cancel
        self.lifecycle.begin_use()
        upstream = None
        try:
            provider, url, model = status["provider"], status["endpoint"], status["model"]
            cfg = next((e for e in settings["endpoints"] if e["url"].rstrip("/") == url.rstrip("/")), None)
            prov = discovery.build_provider(settings, provider, url, cfg)
            ground = req.get("ground", True) is not False
            retrieved, sources, rinfo = self._retrieve(ground, messages, clean_focus(req.get("focus")), params)
            if retrieved and context:
                context = retrieved + "\n\nFile supplied by the user:\n" + context
            elif retrieved:
                context = retrieved
            system = build_system(params, context, grounded_empty=ground and rinfo.get("enabled") and not sources and not context)
            budget = params.context_size - params.max_tokens - _SAFETY_MARGIN_TOKENS
            sent, trimmed = fit_messages(system, messages, max(budget, 128))
            options = to_provider_options(provider, params)
            yield {"type": "start", "id": rid, "provider": provider, "endpoint": url, "model": model,
                   "options": options, "messages": sent, "trimmed": trimmed, "sources": sources, "retrieval": rinfo}
            started = time.monotonic()
            extra = {"keep_alive": f"{int(settings.get('idle_minutes', 10))}m"} if provider == "ollama" else {}
            upstream = prov.stream_chat(model, sent, options, cancel, idle_timeout=self.idle_timeout,
                                        tick_seconds=self.tick_seconds, **extra)
            thinking = False
            for ev in upstream:
                kind = ev["type"]
                if kind == "thinking":
                    if not thinking:
                        thinking = True
                        yield ev
                    continue
                if kind == "token":
                    thinking = False
                if kind == "done":
                    ev = {**ev, "elapsed": round(time.monotonic() - started, 2)}
                yield ev
        except Cancelled:
            yield {"type": "cancelled"}
        except ProviderError as e:
            yield _error(e.kind, str(e))
        except GeneratorExit:
            raise
        except Exception as e:  # a bug here must not take down the server thread silently
            yield _error("internal", f"unexpected error: {type(e).__name__}")
        finally:
            cancel.set()  # also tells the reader thread to let go of the connection
            if upstream is not None:
                upstream.close()
            with self._lock:
                self.active.pop(rid, None)
            self.lifecycle.end_use()
            self._slot.release()
