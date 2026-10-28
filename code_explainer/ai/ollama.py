"""Ollama: native API (https://github.com/ollama/ollama/blob/main/docs/api.md)."""

from __future__ import annotations

from .base import ModelInfo, Provider, as_int, expect_list, looks_like_embedding
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
