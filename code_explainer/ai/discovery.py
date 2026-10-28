"""Finding which local model servers exist and which models they offer."""

from __future__ import annotations

import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import cli_tools
from .http import ProviderError, check_endpoint_url, is_loopback_host
from .lmstudio import LMStudio
from .ollama import Ollama
from .openai_compat import OpenAICompat
from .settings import api_key

PROVIDER_CLASSES = {"ollama": Ollama, "lmstudio": LMStudio, "openai": OpenAICompat}
DEFAULT_ENDPOINTS = [("ollama", Ollama.default_url), ("lmstudio", LMStudio.default_url)]
BINARY = {"ollama": "ollama", "lmstudio": "lms"}


def _is_loopback_url(url: str) -> bool:
    return is_loopback_host(urllib.parse.urlsplit(url).hostname or "")


def _probe(provider: str, url: str, name: str, source: str, key: str | None, allow_remote: bool, timeout: float) -> dict:
    status = {"provider": provider, "endpoint": url, "name": name or PROVIDER_CLASSES[provider].label, "source": source,
              "reachable": False, "error": "", "errorKind": "", "version": "", "installed": False, "binary": "",
              "modelsSource": "none", "models": []}
    binary = cli_tools.find_binary(BINARY[provider]) if provider in BINARY else None
    status["installed"], status["binary"] = bool(binary), binary or ""
    try:
        base = check_endpoint_url(url, allow_remote)
        prov = PROVIDER_CLASSES[provider](base, key, timeout=timeout, allow_remote=allow_remote)
        models = prov.list_models()
        status.update(reachable=True, models=[m.to_dict() for m in models], modelsSource="api")
        try:
            status["version"] = prov.version()
        except ProviderError:
            pass
        return status
    except ProviderError as e:
        status.update(error=str(e), errorKind=e.kind)
    # Not reachable: for a local Ollama / LM Studio, still show what is downloaded.
    if _is_loopback_url(url) and provider in BINARY:
        try:
            models = cli_tools.list_ollama_disk() if provider == "ollama" else cli_tools.list_lmstudio_cli()
        except ProviderError:
            models = []
        if models:
            for m in models:
                m.endpoint = url
            status.update(models=[m.to_dict() for m in models], modelsSource="disk" if provider == "ollama" else "cli")
    return status


def discover(settings: dict, include_defaults: bool = True, timeout: float = 2.0) -> list[dict]:
    """Probe the default local ports and the configured endpoints, in parallel."""
    targets, seen = [], set()
    for ep in settings.get("endpoints", []):
        key = (ep["provider"], ep["url"].rstrip("/"))
        if key not in seen:
            seen.add(key)
            targets.append((ep["provider"], ep["url"], ep.get("name", ""), "config", api_key(ep)))
    if include_defaults:
        for provider, url in DEFAULT_ENDPOINTS:
            if (provider, url) not in seen:
                seen.add((provider, url))
                targets.append((provider, url, "", "default", None))
    allow_remote = bool(settings.get("allow_remote"))
    if not targets:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        futures = [pool.submit(_probe, p, u, n, s, k, allow_remote, timeout) for p, u, n, s, k in targets]
        return [f.result() for f in futures]


def resolve_endpoint(settings: dict, provider: str, endpoint: str) -> tuple[str, dict]:
    """Map what the browser sent to an endpoint we already know about. The browser can pick among
    configured and default endpoints but can never make the server contact an address of its choosing."""
    if provider not in PROVIDER_CLASSES:
        raise ProviderError("blocked", f"unknown provider {provider!r}")
    allow_remote = bool(settings.get("allow_remote"))
    url = check_endpoint_url(str(endpoint or ""), allow_remote)
    known = [(ep["provider"], ep["url"].rstrip("/"), ep) for ep in settings.get("endpoints", [])]
    known += [(p, u.rstrip("/"), {"provider": p, "url": u, "name": "", "api_key_env": ""}) for p, u in DEFAULT_ENDPOINTS]
    for p, u, cfg in known:
        if p == provider and u == url:
            return url, cfg
    raise ProviderError("blocked", "that endpoint is not one of your configured AI servers")


def build_provider(settings: dict, provider: str, url: str, cfg: dict | None = None, timeout: float = 3.0):
    cfg = cfg or {}
    return PROVIDER_CLASSES[provider](url, api_key(cfg) if cfg else None, timeout=timeout,
                                      allow_remote=bool(settings.get("allow_remote")))
