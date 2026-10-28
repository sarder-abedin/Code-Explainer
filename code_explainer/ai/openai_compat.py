"""Any OpenAI-compatible server (llama.cpp, Jan, BionicGPT, vLLM, LocalAI, ...)."""

from __future__ import annotations

from .base import ModelInfo, Provider, expect_list, looks_like_embedding


class OpenAICompat(Provider):
    name = "openai"
    label = "OpenAI-compatible server"
    default_url = ""

    def _path(self, tail: str) -> str:
        # The base URL may or may not already end with /v1.
        return tail if not self.base_url.endswith("/v1") else tail[len("/v1"):]

    def list_models(self) -> list[ModelInfo]:
        data = expect_list(self._get(self._path("/v1/models")), "data")
        return [ModelInfo(id=str(m["id"]), name=str(m["id"]), provider=self.name, endpoint=self.base_url,
                          kind="embedding" if looks_like_embedding(str(m["id"])) else "llm")
                for m in data if isinstance(m, dict) and m.get("id")]
