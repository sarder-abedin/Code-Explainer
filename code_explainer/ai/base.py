"""Shared types for model providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .http import ProviderError, request_json

_EMBED_HINTS = ("embed", "bge-", "minilm", "e5-", "gte-")


@dataclass
class ModelInfo:
    id: str                    # what you pass back to the provider to use it
    name: str                  # human-friendly name
    provider: str
    endpoint: str
    size_bytes: int | None = None
    params: str = ""           # e.g. "7B"
    quant: str = ""            # e.g. "Q4_K_M"
    family: str = ""           # architecture family
    context_max: int | None = None
    loaded: bool | None = None  # None = the provider cannot say
    kind: str = "llm"          # llm | embedding | other

    def to_dict(self) -> dict:
        return asdict(self)


def looks_like_embedding(*names: str) -> bool:
    return any(h in (n or "").lower() for n in names for h in _EMBED_HINTS)


def as_int(value) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


class Provider:
    name = ""
    label = ""
    default_url = ""

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout  # per request, so one stuck server cannot stall discovery

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _get(self, path: str):
        return request_json(self.base_url + path, headers=self._headers(), timeout=self.timeout)

    def version(self) -> str:
        return ""

    def list_models(self) -> list[ModelInfo]:
        raise NotImplementedError

    def context_length(self, model: str) -> int | None:
        return None


def expect_list(obj, key: str) -> list:
    """`obj[key]` as a list; a missing key means empty, a wrong shape is a bad response."""
    if isinstance(obj, dict):
        v = obj.get(key)
        if v is None:
            return []
        if isinstance(v, list):
            return v
    raise ProviderError("bad_response", f"unexpected response shape (expected {key!r} list)")
