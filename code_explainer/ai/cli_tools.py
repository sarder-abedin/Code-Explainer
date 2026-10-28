"""Finding installed backends and listing models while their server is not running.

* Ollama: `ollama list` is only a client of the server, so it cannot help when the server
  is down. Downloaded models are instead read from Ollama's model store on disk (manifest
  files). The layout is ASSUMED from how Ollama stores models, not from its docs.
* LM Studio: `lms ls --json` lists downloaded models. Its JSON schema is not documented, so
  parsing accepts several likely key names.

Commands are run as argument lists (never through a shell) with a timeout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .base import ModelInfo, as_int, looks_like_embedding
from .http import ProviderError

CLI_TIMEOUT = 10.0
MAX_OUTPUT = 4_000_000
_KNOWN_DIRS = {
    "lms": ["~/.lmstudio/bin", "~/.cache/lm-studio/bin"],
    "ollama": ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "~/AppData/Local/Programs/Ollama"],
}


def find_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    exts = ("", ".exe") if os.name == "nt" else ("",)
    for d in _KNOWN_DIRS.get(name, []):
        for ext in exts:
            p = Path(os.path.expanduser(d)) / (name + ext)
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return None


def _run(binary: str, *args: str, timeout: float | None = None) -> str:
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout or CLI_TIMEOUT,
                             stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired as e:
        raise ProviderError("timeout", f"`{Path(binary).name} {' '.join(args)}` took too long") from e
    except OSError as e:
        raise ProviderError("unsupported", f"cannot run {binary}: {e}") from e
    if out.returncode != 0:
        raise ProviderError("bad_response", f"`{Path(binary).name} {' '.join(args)}` failed: "
                                            f"{(out.stderr or out.stdout).strip()[:200]}")
    return out.stdout[:MAX_OUTPUT]


# ----------------------------------------------------------------- LM Studio
def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def parse_lms_json(text: str) -> list[ModelInfo]:
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ProviderError("bad_response", "could not read the output of `lms ls --json`") from e
    if isinstance(data, dict):
        data = data.get("models") or data.get("data") or []
    if not isinstance(data, list):
        raise ProviderError("bad_response", "unexpected output from `lms ls --json`")
    out = []
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = _first(m, "modelKey", "identifier", "path", "name", "id", "key")
        if not mid:
            continue
        q = m.get("quantization")
        kind = str(m.get("type") or "")
        out.append(ModelInfo(
            id=str(mid), name=str(_first(m, "displayName", "display_name", "name") or mid), provider="lmstudio",
            endpoint="", size_bytes=as_int(_first(m, "sizeBytes", "size_bytes", "size")),
            params=str(_first(m, "paramsString", "params_string", "params") or ""),
            quant=str(q.get("name", "")) if isinstance(q, dict) else str(q or ""),
            family=str(_first(m, "architecture", "arch") or ""),
            context_max=as_int(_first(m, "maxContextLength", "max_context_length")),
            kind="embedding" if kind.startswith("embed") or looks_like_embedding(str(mid)) else "llm"))
    return out


def list_lmstudio_cli() -> list[ModelInfo]:
    binary = find_binary("lms")
    if not binary:
        raise ProviderError("unsupported", "the `lms` command is not installed")
    return parse_lms_json(_run(binary, "ls", "--json"))


def lms_load(model: str, context_length: int | None = None) -> None:
    """`lms load <model> [--context-length N]`. Flags are the documented ones; `-y` is not relied on."""
    binary = find_binary("lms")
    if not binary:
        raise ProviderError("unsupported", "the `lms` command is not installed")
    args = ["load", model] + (["--context-length", str(context_length)] if context_length else [])
    _run(binary, *args, timeout=300.0)


def lms_unload(model: str) -> None:
    binary = find_binary("lms")
    if binary:
        _run(binary, "unload", model)


def lms_server_stop() -> None:
    binary = find_binary("lms")
    if binary:
        _run(binary, "server", "stop")


# ----------------------------------------------------------------- Ollama
def ollama_store_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("OLLAMA_MODELS"):
        dirs.append(Path(os.environ["OLLAMA_MODELS"]))
    dirs.append(Path(os.path.expanduser("~/.ollama/models")))
    dirs.append(Path("/usr/share/ollama/.ollama/models"))  # the Linux service install
    return dirs


def scan_ollama_models(root: Path) -> list[ModelInfo]:
    manifests = Path(root) / "manifests"
    out = []
    try:
        files = sorted(p for p in manifests.rglob("*") if p.is_file())
    except OSError:
        return []
    for f in files:
        rel = f.relative_to(manifests).parts
        if len(rel) < 3:
            continue
        *path, tag = rel
        if path[0] == "registry.ollama.ai":
            path = path[1:]
            if path and path[0] == "library":
                path = path[1:]
        if not path:
            continue
        mid = "/".join(path) + ":" + tag
        size = None
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            layers = data.get("layers")
            if isinstance(layers, list):
                size = sum(as_int(l.get("size")) or 0 for l in layers if isinstance(l, dict))
                size += as_int((data.get("config") or {}).get("size")) or 0
        except (OSError, ValueError, AttributeError):
            pass
        out.append(ModelInfo(id=mid, name=mid, provider="ollama", endpoint="", size_bytes=size,
                             kind="embedding" if looks_like_embedding(mid) else "llm"))
    return out


def list_ollama_disk() -> list[ModelInfo]:
    for d in ollama_store_dirs():
        models = scan_ollama_models(d)
        if models:
            return models
    return []
