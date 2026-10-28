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
from .cache import ExplainCache
from .stream import Cancelled
from .tasks import TEMPLATE_VERSION, TaskError, build_task

MAX_CONTEXT_CHARS = 60_000
MAX_RETRIEVAL_TOKENS = 12_000
MAX_RETRIEVAL_SHARE = 0.6   # of the context window; the rest is for the conversation and the answer
MAX_EXPLAIN_TOKENS = 14_000
EXPLAIN_SHARE = 0.75        # an explanation has no conversation, so the code may take more of the window
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
                 tick_seconds: float = 5.0, retriever=None, get_data=None, cache: ExplainCache | None = None):
        self.lifecycle, self._load_settings, self.retriever = lifecycle, load_settings, retriever
        self.get_data, self.cache = get_data, cache
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

    def _acquire(self, cancel: threading.Event | None):
        """Take the single answer slot, or return None when an answer is already being written."""
        if not self._slot.acquire(blocking=False):
            return None
        rid, cancel = secrets.token_hex(8), cancel or threading.Event()
        with self._lock:
            self.active[rid] = cancel
        return rid, cancel

    def _generate(self, rid: str, cancel: threading.Event, status: dict, settings: dict, params: GenParams, prepare,
                  on_done=None) -> Iterator[dict]:
        """Stream one answer. The slot is already held; it is released here however this ends.
        `prepare()` returns (system prompt, messages, extra fields for the start event)."""
        self.lifecycle.begin_use()
        upstream = None
        text: list[str] = []
        try:
            provider, url, model = status["provider"], status["endpoint"], status["model"]
            cfg = next((e for e in settings["endpoints"] if e["url"].rstrip("/") == url.rstrip("/")), None)
            prov = discovery.build_provider(settings, provider, url, cfg)
            system, messages, extra = prepare()
            budget = params.context_size - params.max_tokens - _SAFETY_MARGIN_TOKENS
            sent, trimmed = fit_messages(system, messages, max(budget, 128))
            options = to_provider_options(provider, params)
            yield {"type": "start", "id": rid, "provider": provider, "endpoint": url, "model": model,
                   "options": options, "messages": sent, "trimmed": trimmed, **extra}
            started = time.monotonic()
            keep = {"keep_alive": f"{int(settings.get('idle_minutes', 10))}m"} if provider == "ollama" else {}
            upstream = prov.stream_chat(model, sent, options, cancel, idle_timeout=self.idle_timeout,
                                        tick_seconds=self.tick_seconds, **keep)
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
                    text.append(ev["text"])
                if kind == "done":
                    ev = {**ev, "elapsed": round(time.monotonic() - started, 2)}
                    if on_done and not ev.get("truncated"):
                        on_done("".join(text), ev, options)   # saved before the consumer sees `done`
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
        got = self._acquire(cancel)
        if got is None:
            yield _error("busy", "Another answer is still being written.")
            return
        rid, cancel = got

        def prepare():
            ground = req.get("ground", True) is not False
            retrieved, sources, rinfo = self._retrieve(ground, messages, clean_focus(req.get("focus")), params)
            ctx = (retrieved + "\n\nFile supplied by the user:\n" + context) if retrieved and context else (retrieved or context)
            system = build_system(params, ctx, grounded_empty=ground and rinfo.get("enabled") and not sources and not ctx)
            return system, messages, {"sources": sources, "retrieval": rinfo}

        yield from self._generate(rid, cancel, status, settings, params, prepare)

    # ------------------------------------------------------------ "Explain with AI"
    def explain(self, request: object, cancel: threading.Event | None = None) -> Iterator[dict]:
        """Explain a file, function, flow step or change. A saved answer is served without the model (even when
        the AI is stopped); otherwise the model writes one and, if it finishes cleanly, it is saved."""
        req = request if isinstance(request, dict) else {}
        status = self.lifecycle.status()
        settings = self._load_settings()
        params = clean_params(req.get("params"), GenParams(**settings["params"]))
        if self.get_data is None:
            yield _error("unavailable", "There is no analysis of the project to explain.")
            return
        ready = status["state"] == "ready"
        sel = settings.get("selected", {})
        provider, model = (status["provider"], status["model"]) if ready else (sel.get("provider", ""), sel.get("model", ""))
        free = params.context_size - params.max_tokens - _SAFETY_MARGIN_TOKENS - estimate_tokens(build_system(params)) - 160
        budget = int(min(max(free, 0), params.context_size * EXPLAIN_SHARE, MAX_EXPLAIN_TOKENS))
        try:
            task = build_task(self.get_data()[1], req, budget)
        except TaskError as e:
            yield _error("bad_request", str(e))
            return
        key = None
        if self.cache is not None and provider and model:
            key = ExplainCache.make_key(task.kind, task.target, task.content, provider, model, params.to_dict(), TEMPLATE_VERSION)
            if req.get("regenerate") is not True:
                try:
                    hit = self.cache.get(key)
                except Exception:  # a broken cache is just a miss
                    hit = None
                if hit:
                    yield from self._replay(hit, task, provider, model)
                    return
        if not ready:
            yield _error("not_ready", "The AI Explainer is not running.")
            return
        got = self._acquire(cancel)
        if got is None:
            yield _error("busy", "Another answer is still being written.")
            return
        rid, cancel = got

        def prepare():
            system = build_system(params, task.context)
            return system, [{"role": "user", "content": task.message}], {
                "kind": task.kind, "title": task.title, "cached": False, "sources": task.sources, "retrieval": {"enabled": False}}

        def save(text: str, done: dict, options: dict) -> None:
            if key is None or not text.strip():
                return
            try:
                self.cache.put(key, {"text": text, "stats": done.get("stats", {}), "model": model, "provider": provider,
                                     "sources": task.sources, "options": options, "level": params.level})
            except Exception:
                pass  # failing to save must never affect the answer

        for ev in self._generate(rid, cancel, status, settings, params, prepare, on_done=save):
            yield {**ev, "cached": False} if ev["type"] == "done" else ev

    @staticmethod
    def _replay(entry: dict, task, provider: str, model: str) -> Iterator[dict]:
        yield {"type": "start", "id": secrets.token_hex(8), "provider": provider, "endpoint": "", "model": model,
               "options": entry.get("options", {}), "messages": [], "trimmed": {"dropped": 0, "truncated": False},
               "kind": task.kind, "title": task.title, "cached": True, "sources": entry.get("sources", []),
               "retrieval": {"enabled": False}}
        yield {"type": "token", "text": entry["text"]}
        yield {"type": "done", "truncated": False, "stats": entry.get("stats", {}), "elapsed": 0, "cached": True}
