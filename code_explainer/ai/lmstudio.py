"""LM Studio. Tries the native REST API first (it knows which models are loaded), then the
older /api/v0 endpoint, then the OpenAI-compatible /v1/models (which may list only loaded
models unless just-in-time loading is on)."""

from __future__ import annotations

from .base import ModelInfo, Provider, as_int, expect_list, looks_like_embedding
from .http import ProviderError
from .openai_compat import OpenAIChatMixin

_KIND = {"llm": "llm", "vlm": "llm", "embedding": "embedding", "embeddings": "embedding"}


class LMStudio(OpenAIChatMixin, Provider):
    name = "lmstudio"
    label = "LM Studio"
    default_url = "http://127.0.0.1:1234"

    def _v1(self) -> list[ModelInfo]:
        out = []
        for m in expect_list(self._get("/api/v1/models"), "models"):
            if not isinstance(m, dict) or not (m.get("key") or m.get("id")):
                continue
            mid = m.get("key") or m.get("id")
            q = m.get("quantization")
            out.append(ModelInfo(
                id=mid, name=m.get("display_name") or mid, provider=self.name, endpoint=self.base_url,
                size_bytes=as_int(m.get("size_bytes")), params=str(m.get("params_string") or ""),
                quant=str(q.get("name", "")) if isinstance(q, dict) else str(q or ""),
                family=str(m.get("architecture") or ""), context_max=as_int(m.get("max_context_length")),
                loaded=bool(m.get("loaded_instances")), kind=_KIND.get(str(m.get("type")), "other")))
        return out

    def _v0(self) -> list[ModelInfo]:  # shape ASSUMED from LM Studio's earlier REST API
        out = []
        for m in expect_list(self._get("/api/v0/models"), "data"):
            if not isinstance(m, dict) or not m.get("id"):
                continue
            state = m.get("state")
            out.append(ModelInfo(
                id=m["id"], name=m["id"], provider=self.name, endpoint=self.base_url,
                quant=str(m.get("quantization") or ""), family=str(m.get("arch") or ""),
                context_max=as_int(m.get("max_context_length")),
                loaded=(state == "loaded") if state in ("loaded", "not-loaded") else None,
                kind=_KIND.get(str(m.get("type")), "other")))
        return out

    def _openai(self) -> list[ModelInfo]:
        return [ModelInfo(id=m["id"], name=m["id"], provider=self.name, endpoint=self.base_url,
                          kind="embedding" if looks_like_embedding(m["id"]) else "llm")
                for m in expect_list(self._get("/v1/models"), "data") if isinstance(m, dict) and m.get("id")]

    def list_models(self) -> list[ModelInfo]:
        last: ProviderError | None = None
        for attempt in (self._v1, self._v0, self._openai):
            try:
                return attempt()
            except ProviderError as e:
                if e.kind in ("unreachable", "timeout"):
                    raise  # nothing is listening: the other paths would fail the same way
                last = e
        assert last is not None
        raise last

    # ------------------------------------------------------------ lifecycle (documented REST endpoints)
    supports_load = True

    def load(self, model: str, options: dict, **kw) -> str | None:
        body = {"model": model}
        if options.get("context_size"):
            body["context_length"] = options["context_size"]
        try:
            resp = self._post("/api/v1/models/load", body, timeout=300.0)
        except ProviderError as e:
            if e.status in (404, 405):
                raise ProviderError("unsupported", "this LM Studio version cannot load models over its REST API") from e
            raise
        return str(resp.get("instance_id") or model) if isinstance(resp, dict) else model

    def unload(self, handle: str) -> None:
        self._post("/api/v1/models/unload", {"instance_id": handle}, timeout=30.0)
