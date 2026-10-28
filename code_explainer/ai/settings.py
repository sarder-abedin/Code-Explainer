"""AI settings, stored per user in ~/.code-explainer/ai.json (never in a repository).

The file holds endpoints, the selected model, generation parameters and lifecycle
preferences. Secrets are never stored: a key is referenced by the *name* of an
environment variable (`api_key_env`).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from ..paths import state_dir
from .http import ProviderError, check_endpoint_url
from .params import GenParams, clean_params

PROVIDERS = ("ollama", "lmstudio", "openai")
VERSION = 1
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
MAX_ENDPOINTS = 12


def settings_path() -> Path:
    return state_dir() / "ai.json"


def defaults() -> dict:
    return {
        "version": VERSION,
        "endpoints": [],            # extra endpoints: {"provider", "url", "name", "api_key_env"}
        "allow_remote": False,      # sending code to a non-loopback server needs this explicit opt-in
        "autostart": True,          # start/stop a local backend for the user
        "idle_minutes": 10,         # unload the model after this long without use
        "selected": {"provider": "", "endpoint": "", "model": ""},
        "params": GenParams().to_dict(),
    }


def _clean_endpoint(raw: object, allow_remote: bool) -> dict | None:
    if not isinstance(raw, dict) or raw.get("provider") not in PROVIDERS:
        return None
    try:
        url = check_endpoint_url(str(raw.get("url", "")), allow_remote)
    except ProviderError:
        return None
    env = raw.get("api_key_env")
    env = env if isinstance(env, str) and _ENV_NAME.match(env) else ""
    name = str(raw.get("name") or "")[:60]
    return {"provider": raw["provider"], "url": url, "name": name, "api_key_env": env}


def clean_settings(raw: object, base: dict | None = None) -> dict:
    """Merge `raw` over `base` (or the defaults), dropping unknown keys and bad values."""
    out = json.loads(json.dumps(base if base is not None else defaults()))
    if not isinstance(raw, dict):
        return out
    if isinstance(raw.get("allow_remote"), bool):
        out["allow_remote"] = raw["allow_remote"]
    if isinstance(raw.get("autostart"), bool):
        out["autostart"] = raw["autostart"]
    if "idle_minutes" in raw:
        try:
            out["idle_minutes"] = min(max(int(raw["idle_minutes"]), 1), 240)
        except (TypeError, ValueError):
            pass
    if isinstance(raw.get("endpoints"), list):
        seen, eps = set(), []
        for e in raw["endpoints"][:MAX_ENDPOINTS]:
            c = _clean_endpoint(e, out["allow_remote"])
            if c and (c["provider"], c["url"]) not in seen:
                seen.add((c["provider"], c["url"]))
                eps.append(c)
        out["endpoints"] = eps
    sel = raw.get("selected")
    if isinstance(sel, dict):
        prov = sel.get("provider", "")
        ok = prov in PROVIDERS
        out["selected"] = {
            "provider": prov if ok else "",
            "endpoint": str(sel.get("endpoint", ""))[:300] if ok else "",
            "model": str(sel.get("model", ""))[:300] if ok else "",
        }
    if "params" in raw:
        out["params"] = clean_params(raw["params"], GenParams(**out["params"])).to_dict()
    # A selected endpoint that is no longer allowed must not stay selected.
    if out["selected"]["endpoint"]:
        try:
            check_endpoint_url(out["selected"]["endpoint"], out["allow_remote"])
        except ProviderError:
            out["selected"] = {"provider": "", "endpoint": "", "model": ""}
    out["version"] = VERSION
    return out


def load() -> dict:
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults()
    return clean_settings(raw)


def save(settings: dict) -> dict:
    cleaned = clean_settings(settings)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return cleaned


def update(patch: object) -> dict:
    """Apply a partial update from the UI and persist it."""
    return save(clean_settings(patch, load()))


def api_key(endpoint: dict) -> str | None:
    env = endpoint.get("api_key_env") or ""
    return (os.environ.get(env) or None) if env else None
