"""Starting, loading, idling and stopping a local model backend, on request.

Rules the code below keeps:
* Nothing runs until the user asks (`start`).
* The browser can only pick among known endpoints (see discovery.resolve_endpoint).
* We only stop what we started: a backend we launched, a model we loaded. A server that was
  already running, or a model the user had loaded, is left exactly as we found it.
* Backends are launched as fixed argument lists (never a shell), only for loopback endpoints,
  with output going to a private log file outside any repository.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

from ..paths import state_dir
from . import cli_tools, discovery
from . import settings as ai_settings
from .http import ProviderError, is_loopback_host
from .params import GenParams

_PROGRAMS = {"ollama": "ollama", "lmstudio": "lms"}
_WARN_CAP = 8


class _Stale(Exception):
    """This start attempt was superseded or cancelled."""


def _hint_not_running(provider: str, url: str) -> str:
    return {
        "ollama": "Start Ollama (run `ollama serve`) or turn on auto-start in the AI settings.",
        "lmstudio": "Start the LM Studio local server (Developer tab, or `lms server start`) or turn on auto-start in the AI settings.",
    }.get(provider, f"Start your model server at {url}.")


class Lifecycle:
    def __init__(self, start_timeout: float = 30.0, probe_timeout: float = 2.0, load_settings=ai_settings.load):
        self.start_timeout, self.probe_timeout, self._load_settings = start_timeout, probe_timeout, load_settings
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._gen = 0
        self._proc: subprocess.Popen | None = None
        self._last_pid: int | None = None
        self._busy = 0
        self._reset()

    # ------------------------------------------------------------ state
    def _reset(self) -> None:
        self._st = {"state": "off", "step": "", "provider": "", "endpoint": "", "model": "", "label": "",
                    "error": "", "errorKind": "", "hint": "", "startedBackend": False, "loadedByUs": False,
                    "since": time.time(), "warnings": []}
        self._handle: str | None = None
        self._cli_loaded = False
        self._last_used = time.time()
        self._idle_s = 600
        self._proc = None

    def _set(self, gen: int, **kw) -> None:
        with self._lock:
            if gen != self._gen:
                raise _Stale()
            self._st.update(kw)
            self._st["since"] = time.time()

    def _warn(self, msg: str) -> None:
        with self._lock:
            if msg not in self._st["warnings"] and len(self._st["warnings"]) < _WARN_CAP:
                self._st["warnings"].append(msg)

    def status(self) -> dict:
        with self._lock:
            st = dict(self._st, warnings=list(self._st["warnings"]))
            st["idleSeconds"] = self._idle_s
            st["secondsToIdle"] = (max(0, round(self._idle_s - (time.time() - self._last_used)))
                                   if st["state"] == "ready" else None)
            st["busy"] = self._busy > 0
            return st

    # ------------------------------------------------------------ public API
    def start(self, provider: str, endpoint: str, model: str) -> dict:
        settings = self._load_settings()
        url, cfg = discovery.resolve_endpoint(settings, provider, endpoint)
        model = str(model or "").strip()
        if not model or len(model) > 300 or any(ord(c) < 32 for c in model):
            raise ProviderError("blocked", "choose a model first")
        with self._lock:
            st = self._st
            same = (st["provider"], st["endpoint"], st["model"]) == (provider, url, model)
            if same and st["state"] in ("starting", "loading", "ready"):
                return self.status()
            previous = dict(st), self._handle, self._cli_loaded, self._proc
            self._cancel.set()
            self._gen += 1
            gen, cancel = self._gen, threading.Event()
            self._cancel = cancel
            self._reset_for_new(previous, provider, url, model, cfg)
        worker = threading.Thread(target=self._run, args=(gen, cancel, provider, url, cfg, model, settings, previous),
                                  name="ai-lifecycle", daemon=True)
        self._worker = worker
        worker.start()
        return self.status()

    def _reset_for_new(self, previous, provider, url, model, cfg) -> None:
        old_state = previous[0]
        keep_backend = old_state["startedBackend"] and old_state["endpoint"] == url and old_state["provider"] == provider
        proc = self._proc if keep_backend else None
        self._reset()
        self._proc = proc
        self._st.update(state="starting", step="Checking the model server…", provider=provider, endpoint=url, model=model,
                        label=cfg.get("name") or discovery.PROVIDER_CLASSES[provider].label,
                        startedBackend=keep_backend)

    def wait(self, timeout: float = 30.0) -> dict:
        worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout)
        return self.status()

    def stop(self, reason: str = "") -> dict:
        with self._lock:
            self._cancel.set()
            self._gen += 1
            worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(10)
        with self._lock:
            snap = dict(self._st)
            handle, cli_loaded, proc = self._handle, self._cli_loaded, self._proc
            settings = self._load_settings()
        warnings = [reason] if reason else []
        if snap["state"] != "off" or proc is not None:
            warnings += self._teardown(settings, snap, handle, cli_loaded)
        with self._lock:
            self._reset()
            self._st["warnings"] = warnings[:_WARN_CAP]
        return self.status()

    def shutdown(self) -> None:
        self.stop()

    def begin_use(self) -> None:
        with self._lock:
            self._busy += 1
            self._last_used = time.time()

    def end_use(self) -> None:
        with self._lock:
            self._busy = max(0, self._busy - 1)
            self._last_used = time.time()

    def touch(self) -> None:
        with self._lock:
            self._last_used = time.time()

    def check_idle(self, now: float | None = None) -> bool:
        """Stop everything we started if nothing has used it for the configured time."""
        now = time.time() if now is None else now
        with self._lock:
            if self._st["state"] != "ready" or self._busy > 0:
                return False
            if now - self._last_used < self._idle_s:
                return False
            minutes = round(self._idle_s / 60)
        self.stop(reason=f"Stopped after {minutes} minute{'s' if minutes != 1 else ''} idle to free memory.")
        return True

    def watch(self, stop: threading.Event, every: float = 15.0) -> None:
        while not stop.wait(every):
            try:
                self.check_idle()
            except Exception:
                pass  # a failed idle check must never kill the watcher

    # ------------------------------------------------------------ the start worker
    def _run(self, gen, cancel, provider, url, cfg, model, settings, previous) -> None:
        try:
            old, old_handle, old_cli, _ = previous
            if old["state"] != "off" and old["loadedByUs"] and old["model"] and (old["provider"], old["endpoint"]) == (provider, url):
                self._unload_previous(settings, old, old_handle, old_cli)
            elif old["state"] != "off" and (old["provider"], old["endpoint"]) != (provider, url):
                self._teardown(settings, old, old_handle, old_cli, keep_proc=False)
            prov = discovery.build_provider(settings, provider, url, cfg, timeout=self.probe_timeout)
            self._set(gen, step=f"Checking {self._st['label']}…")
            models = self._ensure_backend(gen, cancel, settings, provider, url, prov)
            info = self._find_model(models, provider, model)
            if info is None and provider != "openai":
                hint = (f"Download it first: `ollama pull {model}`" if provider == "ollama"
                        else "Download the model in LM Studio first (or run `lms get`).")
                raise _Hinted("model_missing", f"{model} is not downloaded on {self._st['label']}.", hint)
            real_model = info.id if info else model
            params = GenParams(**settings["params"])
            if prov.supports_load and (info is None or info.loaded is not True):
                self._set(gen, state="loading", step=f"Loading {real_model} into memory…", model=real_model)
                self._load(gen, settings, provider, prov, real_model, params)
            with self._lock:
                if gen != self._gen:
                    raise _Stale()
                self._idle_s = int(settings.get("idle_minutes", 10)) * 60
                self._last_used = time.time()
                self._st.update(state="ready", step="Ready", model=real_model, since=time.time())
        except _Stale:
            return
        except _Hinted as e:
            self._fail(gen, settings, e.kind, str(e), e.hint)
        except ProviderError as e:
            self._fail(gen, settings, e.kind, str(e), "")

    def _fail(self, gen, settings, kind, message, hint) -> None:
        with self._lock:
            if gen != self._gen:
                return
            snap, handle, cli_loaded = dict(self._st), self._handle, self._cli_loaded
        # Clean up what this attempt started, then report.
        warnings = self._teardown(settings, snap, handle, cli_loaded)
        with self._lock:
            if gen != self._gen:
                return
            self._st.update(state="error", step="", error=message, errorKind=kind, hint=hint, startedBackend=False,
                            loadedByUs=False, since=time.time())
            self._st["warnings"] = (self._st["warnings"] + warnings)[:_WARN_CAP]
            self._proc = None

    @staticmethod
    def _find_model(models, provider, model):
        by_id = {m.id: m for m in models}
        if model in by_id:
            return by_id[model]
        if provider == "ollama" and ":" not in model:
            return by_id.get(model + ":latest")
        return None

    # ------------------------------------------------------------ backend
    def _probe(self, prov):
        return prov.list_models()

    def _ensure_backend(self, gen, cancel, settings, provider, url, prov):
        try:
            return self._probe(prov)
        except ProviderError as e:
            if e.kind not in ("unreachable", "timeout"):
                raise
            first = e
        host = urllib.parse.urlsplit(url).hostname or ""
        if not settings.get("autostart"):
            raise _Hinted("not_running", f"{self._st['label']} is not running.", _hint_not_running(provider, url))
        if not is_loopback_host(host):
            raise _Hinted("not_running", f"{url} is not reachable, and the tool only starts servers on this computer.",
                          _hint_not_running(provider, url))
        program = _PROGRAMS.get(provider)
        binary = cli_tools.find_binary(program) if program else None
        if not binary:
            hint = ("Install Ollama from https://ollama.com, then try again." if provider == "ollama" else
                    "Install LM Studio (https://lmstudio.ai) and its `lms` command, then try again."
                    if provider == "lmstudio" else _hint_not_running(provider, url))
            raise _Hinted("not_installed", f"{self._st['label']} is not installed (or not on PATH).", hint)
        self._set(gen, step=f"Starting {self._st['label']}…")
        self._spawn(gen, provider, binary, url)
        return self._wait_healthy(gen, cancel, provider, prov, first)

    def _spawn(self, gen, provider: str, binary: str, url: str) -> None:
        parts = urllib.parse.urlsplit(url)
        host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname
        env = dict(os.environ)
        if provider == "ollama":
            argv = [binary, "serve"]
            env["OLLAMA_HOST"] = f"{host}:{parts.port or 11434}"
        else:
            argv = [binary, "server", "start", "--port", str(parts.port or 1234)]
        log_dir = state_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(log_dir / f"{provider}.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, env=env, start_new_session=True)
        except OSError as e:
            raise ProviderError("start_failed", f"could not start {Path(binary).name}: {e}") from e
        finally:
            os.close(fd)
        with self._lock:
            self._proc, self._last_pid = proc, proc.pid
            stale = gen != self._gen
            if not stale:
                self._st["startedBackend"] = True
        if stale:
            self._kill_proc(proc)
            raise _Stale()

    def _log_tail(self, provider: str) -> str:
        try:
            text = (state_dir() / "logs" / f"{provider}.log").read_text(errors="replace")
        except OSError:
            return ""
        return " ".join(text.strip().splitlines()[-4:])[:400]

    def _wait_healthy(self, gen, cancel, provider, prov, first_error):
        deadline = time.monotonic() + self.start_timeout
        while True:
            if cancel.is_set() or gen != self._gen:
                raise _Stale()
            proc = self._proc
            code = proc.poll() if proc else None
            # `ollama serve` must keep running; `lms server start` may hand off to a daemon and exit 0.
            if code is not None and (provider == "ollama" or code != 0):
                raise ProviderError("start_failed", f"{self._st['label']} stopped right after starting: "
                                                    f"{self._log_tail(provider) or f'exit code {code}'}")
            try:
                return self._probe(prov)
            except ProviderError as e:
                if e.kind not in ("unreachable", "timeout"):
                    raise
            if time.monotonic() > deadline:
                raise ProviderError("start_timeout", f"{self._st['label']} did not become ready within "
                                                     f"{self.start_timeout:.0f} seconds. {self._log_tail(provider)}".strip())
            time.sleep(0.2)

    @staticmethod
    def _kill_proc(proc: subprocess.Popen | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                pass

    # ------------------------------------------------------------ model load / unload
    def _load(self, gen, settings, provider, prov, model, params: GenParams) -> None:
        ctx = params.context_size
        idle = f"{int(settings.get('idle_minutes', 10))}m"
        try:
            handle = prov.load(model, {"num_ctx": ctx} if provider == "ollama" else {"context_size": ctx}, keep_alive=idle)
        except ProviderError as e:
            if provider == "lmstudio" and e.kind == "unsupported":
                try:
                    cli_tools.lms_load(model, ctx)
                except ProviderError:
                    self._warn("Could not load the model ahead of time; LM Studio's just-in-time loading will load it on first use.")
                    return
                with self._lock:
                    self._cli_loaded, self._st["loadedByUs"] = True, True
                return
            raise _Hinted("load_failed", f"could not load {model}: {e}", "Try a smaller model or a smaller context size.") from e
        with self._lock:
            if gen != self._gen:
                raise _Stale()
            self._handle, self._st["loadedByUs"] = handle, True

    def _unload_previous(self, settings, old, handle, cli_loaded) -> None:
        self._teardown(settings, old, handle, cli_loaded, keep_proc=True)

    def _teardown(self, settings, snap, handle, cli_loaded, keep_proc: bool = False) -> list[str]:
        """Undo what we did: unload the model we loaded, stop the backend we started. Returns warnings."""
        warnings: list[str] = []
        provider, url = snap.get("provider"), snap.get("endpoint")
        if snap.get("loadedByUs") and snap.get("model") and provider:
            try:
                if cli_loaded:
                    cli_tools.lms_unload(snap["model"])
                else:
                    prov = discovery.build_provider(settings, provider, url, timeout=5.0,
                                                    cfg=next((e for e in settings.get("endpoints", []) if e["url"].rstrip("/") == url.rstrip("/")), None))
                    prov.unload(handle or snap["model"])
            except ProviderError as e:
                warnings.append(f"Could not unload {snap['model']}: {e}")
        if snap.get("startedBackend") and not keep_proc:
            warnings += self._stop_backend(provider)
        return warnings

    def _stop_backend(self, provider: str | None) -> list[str]:
        warnings: list[str] = []
        if provider == "lmstudio":
            try:
                cli_tools.lms_server_stop()
            except ProviderError as e:
                warnings.append(f"Could not stop the LM Studio server cleanly: {e}")
        proc = self._proc
        self._kill_proc(proc)
        with self._lock:
            self._proc = None
        return warnings


class _Hinted(ProviderError):
    """A ProviderError that carries a user-facing hint."""

    def __init__(self, kind: str, message: str, hint: str = ""):
        super().__init__(kind, message)
        self.hint = hint
