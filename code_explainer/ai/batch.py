"""`analyze --ai`: have the local model explain the most important files and changes ahead of time, and
embed the answers in the static report.

It reuses the "Explain with AI" machinery (`ChatService.explain`), so the prompts are the same and every
answer lands in the same saved-answer cache. That cache is what makes a run resumable: stop it whenever
you like, run the same command again, and everything already written is reused instead of asked again.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import discovery
from . import settings as ai_settings
from .http import ProviderError
from .params import GenParams
from .runtime import AIRuntime
from .settings import PROVIDERS

DEFAULT_LIMIT = 10
MAX_PARTS = 2
START_WAIT_SECONDS = 600.0         # loading a large model into memory can take minutes
MAX_ITEM_CHARS = 20_000
_STOP_KINDS = ("not_ready", "unreachable")      # the model is gone: trying the next file would only fail the same way
_CUT_OFF = "The answer was cut off at the length limit. Raise “Max answer length” in the AI tab and run again."


class BatchError(Exception):
    """The run can not even start (no model chosen, model missing, server off)."""


@dataclass
class BatchResult:
    items: dict = field(default_factory=dict)       # "kind|path" -> {kind, path, title, text}
    generated: int = 0                              # written by the model just now
    reused: int = 0                                 # served from saved answers
    failed: list = field(default_factory=list)      # {kind, path, message}
    stopped: bool = False                           # Ctrl-C or cancelled; the rest was not attempted
    aborted: str = ""                               # why the whole run ended early (model went away)
    level: str = ""


def target_key(kind: str, path: str) -> str:
    return f"{kind}|{path}"


# ----------------------------------------------------------------- what to explain
def _change_targets(data: dict) -> list[dict]:
    changes = data.get("changes")
    if not changes:
        return []
    rows = []
    for path, e in changes.get("files", {}).items():
        if e.get("binary"):
            continue
        rows.append((-len(e.get("blast", [])), -(e.get("added", 0) + e.get("deleted", 0)), path, e))
    rows.sort(key=lambda r: r[:3])
    return [{"kind": "change", "path": path, "why": f"changed in this review (+{e.get('added', 0)} / -{e.get('deleted', 0)})"}
            for _, _, path, e in rows]


def pick_targets(data: dict, limit: int) -> list[dict]:
    """What a reviewer most needs first: the changed files that touch the most other code (at most half of
    the budget, so the project's own entry points are still covered), then the reading order of the project."""
    if limit <= 0:
        return []
    known = {f["path"] for f in data["files"]}
    files = [{"kind": "file", "path": r["path"], "why": r["why"]} for r in data.get("readingOrder", []) if r["path"] in known]
    changes = _change_targets(data)
    n_files = min(len(files), limit - min(len(changes), math.ceil(limit / 2)))
    n_changes = min(len(changes), limit - n_files)
    return changes[:n_changes] + files[:n_files]


PART_SHARE = 5            # one part of the map for every this many targets, at most MAX_PARTS


def pick_part_targets(data: dict, limit: int) -> list[dict]:
    """The biggest parts of the architecture map, so the Map can explain itself in a static report."""
    comps = (data.get("architecture") or {}).get("components", [])
    n = min(MAX_PARTS, limit // PART_SHARE)
    big = sorted(comps, key=lambda c: (-c["files"], -c["loc"], c["id"]))[:n]
    return [{"kind": "part", "path": c["id"], "part": c["id"], "why": f"a main part of the project ({c['files']} files)"} for c in big]


def pick_all(data: dict, limit: int) -> list[dict]:
    """Files and changes first, then the main parts of the map; together never more than `limit`."""
    parts = pick_part_targets(data, limit)
    return pick_targets(data, limit - len(parts)) + parts


# ----------------------------------------------------------------- running
def _explain_one(chat, target: dict, params: dict, cancel: threading.Event) -> dict:
    """One target through ChatService.explain, boiled down to an outcome."""
    # ChatService sets the event it is given when an answer ends, so each answer gets its own; the batch's own
    # cancel is checked on every event (the model server sends keep-alive ticks even while it thinks).
    mine = threading.Event()
    gen = chat.explain({**{k: v for k, v in target.items() if k != "why"}, "params": params}, mine)
    title, parts, cached, done = "", [], False, None
    try:
        for ev in gen:
            if cancel.is_set():
                mine.set()
            kind = ev["type"]
            if kind == "start":
                title, cached = ev.get("title", ""), bool(ev.get("cached"))
            elif kind == "token":
                parts.append(ev["text"])
            elif kind == "done":
                done = ev
            elif kind == "cancelled":
                return {"outcome": "cancelled"}
            elif kind == "error":
                msg = ev.get("message", "the model failed")
                return {"outcome": "error", "kind": ev.get("kind", ""), "message": msg + (" " + ev["hint"] if ev.get("hint") else "")}
    finally:
        gen.close()      # releases the answer slot even when we are interrupted
    text = "".join(parts)
    if done is None:
        return {"outcome": "error", "kind": "", "message": "The model stopped before it finished."}
    if done.get("truncated"):
        return {"outcome": "error", "kind": "truncated", "message": _CUT_OFF}
    if not text.strip():
        return {"outcome": "error", "kind": "empty", "message": "The model gave no answer."}
    return {"outcome": "ok", "title": title, "text": text[:MAX_ITEM_CHARS], "cached": cached or bool(done.get("cached"))}


def run_batch(chat, targets: list[dict], params: dict, cancel: threading.Event | None = None, progress=None) -> BatchResult:
    """Explain each target in turn. `progress(index, total, target, state, note)` is called with state
    "start", then "generated", "reused" or "failed". Ctrl-C ends the run cleanly with what is finished."""
    cancel = cancel or threading.Event()
    result = BatchResult()
    total = len(targets)

    def tell(i, target, state, note=""):
        if progress:
            progress(i, total, target, state, note)

    try:
        for i, target in enumerate(targets, 1):
            if cancel.is_set():
                result.stopped = True
                break
            tell(i, target, "start")
            out = _explain_one(chat, target, params, cancel)
            if out["outcome"] == "cancelled":
                result.stopped = True
                break
            if out["outcome"] == "error":
                if out["kind"] in _STOP_KINDS:
                    result.aborted = out["message"]
                    break
                result.failed.append({"kind": target["kind"], "path": target["path"], "message": out["message"]})
                tell(i, target, "failed", out["message"])
                continue
            result.items[target_key(target["kind"], target["path"])] = {
                "kind": target["kind"], "path": target["path"], "title": out["title"], "text": out["text"]}
            if out["cached"]:
                result.reused += 1
            else:
                result.generated += 1
            tell(i, target, "reused" if out["cached"] else "generated")
    except KeyboardInterrupt:
        cancel.set()
        result.stopped = True
    return result


# ----------------------------------------------------------------- the model
def choose_model(settings: dict, spec: str | None) -> tuple[str, str, str]:
    """(provider, endpoint, model): the one picked in the AI tab, or `--ai-model [provider:]name`."""
    if not spec:
        sel = settings.get("selected") or {}
        if sel.get("provider") and sel.get("endpoint") and sel.get("model"):
            return sel["provider"], sel["endpoint"], sel["model"]
        raise BatchError("No AI model is chosen yet. Pick one in the AI tab of `code-explainer serve`, or name one with --ai-model.")
    spec = spec.strip()
    head, sep, rest = spec.partition(":")
    provider, model = (head, rest) if sep and head in PROVIDERS else (None, spec)
    seen, available, embedding = set(), [], False
    for probe in discovery.discover(settings):
        if provider and probe["provider"] != provider:
            continue
        for m in probe["models"]:
            if m["id"] == model:
                if m["kind"] == "llm":
                    return probe["provider"], probe["endpoint"], m["id"]
                embedding = True
            elif m["kind"] == "llm" and m["id"] not in seen:
                seen.add(m["id"])
                available.append(m["id"])
    if embedding:
        raise BatchError(f"{model} is an embedding model; it can not explain code. Choose a chat model.")
    listing = ", ".join(available[:12]) if available else "none (is Ollama or LM Studio installed and are models downloaded?)"
    raise BatchError(f"I can not find a model called {model!r}. Models I can see: {listing}.")


def attach(data: dict, result: BatchResult, provider: str, model: str, level: str) -> None:
    """Put the explanations into the analysis; the report shows them read-only and labelled."""
    if not result.items:
        return
    data["aiExplain"] = {"provider": provider, "model": model, "level": level,
                         "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "items": result.items}


def generate(data: dict, cache_dir, spec: str | None, limit: int, level: str | None, progress=None,
             cancel: threading.Event | None = None) -> BatchResult:
    """Start the chosen model, explain the most important targets, stop what we started, and attach the answers to `data`."""
    settings = ai_settings.load()
    provider, endpoint, model = choose_model(settings, spec)
    level = level or GenParams(**settings["params"]).level
    targets = pick_all(data, limit)
    if not targets:
        return BatchResult(level=level)
    runtime = AIRuntime(get_data=lambda: (0, data), cache_dir=cache_dir)
    try:
        try:
            runtime.start(provider, endpoint, model)
            status = runtime.lifecycle.wait(START_WAIT_SECONDS)
        except ProviderError as e:
            raise BatchError(str(e)) from e
        except KeyboardInterrupt:
            raise BatchError("Stopped before the model was ready.") from None
        if status["state"] != "ready":
            raise BatchError((status.get("error") or "The model did not start.") + (" " + status["hint"] if status.get("hint") else ""))
        result = run_batch(runtime.chat, targets, {"level": level}, cancel=cancel, progress=progress)
    finally:
        runtime.shutdown()       # unloads and stops only what this run started
    result.level = level
    attach(data, result, provider, status.get("model") or model, level)
    return result
