"""Any OpenAI-compatible server (llama.cpp, Jan, BionicGPT, vLLM, LocalAI, ...), and the shared
chat-completions streaming that LM Studio reuses."""

from __future__ import annotations

import threading
from typing import Iterator

from .base import ModelInfo, Provider, expect_list, looks_like_embedding, parse_json_line
from .http import ProviderError


class OpenAIChatMixin:
    """/v1/chat/completions streaming (server-sent events). Needs `_path`, `_stream` from Provider."""

    def _path(self, tail: str) -> str:
        # The base URL may or may not already end with /v1.
        return tail if not self.base_url.endswith("/v1") else tail[len("/v1"):]

    def stream_chat(self, model, messages, options, cancel: threading.Event, *, idle_timeout=120.0, tick_seconds=5.0,
                    **kw) -> Iterator[dict]:
        body = {"model": model, "messages": messages, "stream": True, **options}
        got_text = finished = saw_done_marker = truncated = False
        for line in self._stream(self._path("/v1/chat/completions"), body, cancel, idle_timeout, tick_seconds).lines():
            if line is None:
                yield {"type": "tick"}
                continue
            text = line.decode("utf-8", "replace").strip()
            if not text.startswith("data:"):
                continue  # SSE comments, `event:` lines and blank separators
            payload = text[5:].strip()
            if payload == "[DONE]":
                saw_done_marker = True
                break
            obj = parse_json_line(payload.encode())
            if not isinstance(obj, dict):
                continue
            if obj.get("error"):
                err = obj["error"]
                raise ProviderError("http", str(err.get("message") if isinstance(err, dict) else err))
            for choice in obj.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                if delta.get("reasoning_content") and not delta.get("content"):
                    yield {"type": "thinking"}
                if delta.get("content"):
                    got_text = True
                    yield {"type": "token", "text": delta["content"]}
                if choice.get("finish_reason"):
                    finished = True
                    truncated = choice["finish_reason"] == "length"
        if not got_text:
            raise ProviderError("bad_response", "the model returned an empty answer")
        if not (finished or saw_done_marker):
            raise ProviderError("bad_response", "the connection closed before the answer finished")
        yield {"type": "done", "truncated": truncated, "stats": {}}


class OpenAICompat(OpenAIChatMixin, Provider):
    name = "openai"
    label = "OpenAI-compatible server"
    default_url = ""

    def list_models(self) -> list[ModelInfo]:
        data = expect_list(self._get(self._path("/v1/models")), "data")
        return [ModelInfo(id=str(m["id"]), name=str(m["id"]), provider=self.name, endpoint=self.base_url,
                          kind="embedding" if looks_like_embedding(str(m["id"])) else "llm")
                for m in data if isinstance(m, dict) and m.get("id")]
