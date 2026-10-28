"""Ollama: native API (https://github.com/ollama/ollama/blob/main/docs/api.md)."""

from __future__ import annotations

import threading
from typing import Iterator

from .base import ModelInfo, Provider, as_int, expect_list, looks_like_embedding, parse_json_line
from .http import ProviderError, request_json


class Ollama(Provider):
    name = "ollama"
    label = "Ollama"
    default_url = "http://127.0.0.1:11434"

    def version(self) -> str:
        v = self._get("/api/version")
        return str(v.get("version", "")) if isinstance(v, dict) else ""

    def list_models(self) -> list[ModelInfo]:
        tags = expect_list(self._get("/api/tags"), "models")
        try:
            loaded = {m.get("name") or m.get("model") for m in expect_list(self._get("/api/ps"), "models")
                      if isinstance(m, dict)}
        except ProviderError:
            loaded = None  # cannot tell which are loaded; do not claim "not loaded"
        out = []
        for m in tags:
            if not isinstance(m, dict):
                continue
            mid = m.get("model") or m.get("name")
            if not mid:
                continue
            d = m.get("details") if isinstance(m.get("details"), dict) else {}
            family = str(d.get("family") or "")
            out.append(ModelInfo(
                id=mid, name=m.get("name") or mid, provider=self.name, endpoint=self.base_url,
                size_bytes=as_int(m.get("size")), params=str(d.get("parameter_size") or ""),
                quant=str(d.get("quantization_level") or ""), family=family,
                loaded=None if loaded is None else (mid in loaded or m.get("name") in loaded),
                kind="embedding" if looks_like_embedding(mid) or "bert" in family else "llm"))
        return out

    def context_length(self, model: str) -> int | None:
        info = request_json(self.base_url + "/api/show", method="POST", body={"model": model}, timeout=5.0)
        mi = info.get("model_info") if isinstance(info, dict) else None
        if isinstance(mi, dict):
            for k, v in mi.items():
                if k.endswith(".context_length"):
                    return as_int(v)
        return None

    # ------------------------------------------------------------ lifecycle and chat
    supports_load = True

    def load(self, model: str, options: dict, keep_alive: str | int = "10m", **kw) -> str | None:
        """An empty chat loads the model without generating (applies num_ctx)."""
        body = {"model": model, "messages": [], "keep_alive": keep_alive}
        if options:
            body["options"] = options
        self._post("/api/chat", body, timeout=300.0)
        return model

    def unload(self, handle: str) -> None:
        self._post("/api/chat", {"model": handle, "messages": [], "keep_alive": 0}, timeout=30.0)

    def stream_chat(self, model, messages, options, cancel: threading.Event, *, idle_timeout=120.0, tick_seconds=5.0,
                    keep_alive: str | int | None = None, **kw) -> Iterator[dict]:
        body = {"model": model, "messages": messages, "stream": True, "options": options}
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        got_text = False
        for line in self._stream("/api/chat", body, cancel, idle_timeout, tick_seconds).lines():
            if line is None:
                yield {"type": "tick"}
                continue
            obj = parse_json_line(line)
            if not isinstance(obj, dict):
                continue  # blank or unreadable line
            if obj.get("error"):
                raise ProviderError("http", str(obj["error"]))
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            if msg.get("thinking") and not msg.get("content"):
                yield {"type": "thinking"}
            if msg.get("content"):
                got_text = True
                yield {"type": "token", "text": msg["content"]}
            if obj.get("done"):
                evals, dur = as_int(obj.get("eval_count")), as_int(obj.get("eval_duration"))
                yield {"type": "done", "truncated": obj.get("done_reason") == "length",
                       "stats": {"prompt_tokens": as_int(obj.get("prompt_eval_count")), "completion_tokens": evals,
                                 "tokens_per_second": round(evals / dur * 1e9, 1) if evals and dur else None}}
                return
        raise ProviderError("bad_response", "the connection closed before the answer finished" if got_text
                            else "the model returned an empty answer")
