import json
import os
import time

import pytest

import fake_backends
from ai_fakes import FakeServer, free_port, ollama_routes
from code_explainer.ai import settings
from code_explainer.ai.http import ProviderError
from code_explainer.ai.lifecycle import Lifecycle


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_EXPLAINER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("PATH", f"{tmp_path / 'emptybin'}{os.pathsep}/usr/bin{os.pathsep}/bin")  # no real ollama / lms
    (tmp_path / "emptybin").mkdir()
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "fake.log"))
    monkeypatch.delenv("FAKE_PRELOADED", raising=False)
    saved = dict(os.environ)            # several tests set variables directly; never let them leak into the next
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def lc():
    made = []

    def make(**over):
        l = Lifecycle(**over)
        made.append(l)
        return l
    yield make
    for l in made:
        l.shutdown()


@pytest.fixture
def servers():
    started = []

    def make(routes):
        s = FakeServer(routes)
        started.append(s)
        return s
    yield make
    for s in started:
        s.stop()


def log_of(tmp_path):
    p = tmp_path / "fake.log"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def ops(tmp_path, kind=None):
    return [(e["op"], e.get("model")) for e in log_of(tmp_path) if e["op"] in ("load", "unload") and (not kind or e["server"] == kind)]


def configure(url, provider="ollama", **extra):
    settings.update({"endpoints": [{"provider": provider, "url": url}], **extra})


def start(lifecycle, provider, url, model, timeout=15):
    lifecycle.start(provider, url, model)
    return lifecycle.wait(timeout)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ----------------------------------------------------------------- already running
def test_running_server_loads_the_model_and_unloads_it_on_stop(lc, servers):
    s = servers(ollama_routes({("POST", "/api/chat"): {"done": True}}))
    configure(s.url, autostart=False)
    l = lc()
    st = start(l, "ollama", s.url, "qwen2.5-coder:7b")
    assert st["state"] == "ready" and st["loadedByUs"] is True and st["startedBackend"] is False
    load = [r["body"] for r in s.requests if r["path"] == "/api/chat"][0]
    assert load["messages"] == [] and load["model"] == "qwen2.5-coder:7b" and load["options"]["num_ctx"] == 4096
    assert load["keep_alive"] == "10m"
    st = l.stop()
    assert st["state"] == "off"
    unload = [r["body"] for r in s.requests if r["path"] == "/api/chat"][-1]
    assert unload == {"model": "qwen2.5-coder:7b", "messages": [], "keep_alive": 0}


def test_a_model_the_user_already_loaded_is_never_unloaded(lc, servers):
    s = servers(ollama_routes({("POST", "/api/chat"): {"done": True}}))          # ps says llama3.2:3b is loaded
    configure(s.url)
    l = lc()
    st = start(l, "ollama", s.url, "llama3.2:3b")
    assert st["state"] == "ready" and st["loadedByUs"] is False
    l.stop()
    assert not [r for r in s.requests if r["path"] == "/api/chat"]                  # no load, no unload


def test_a_model_that_is_not_downloaded_is_a_clear_error(lc, servers):
    s = servers(ollama_routes())
    configure(s.url)
    st = start(lc(), "ollama", s.url, "mystery:1b")
    assert st["state"] == "error" and st["errorKind"] == "model_missing"
    assert "ollama pull mystery:1b" in st["hint"]


def test_generic_openai_server_has_nothing_to_load(lc, servers):
    s = servers({("GET", "/v1/models"): {"data": [{"id": "my-model"}]}})
    configure(s.url, provider="openai")
    st = start(lc(), "openai", s.url, "my-model")
    assert st["state"] == "ready" and st["loadedByUs"] is False and [r["method"] for r in s.requests] == ["GET"]


def test_unknown_endpoints_are_refused_so_the_browser_cannot_point_us_anywhere(lc):
    configure("http://127.0.0.1:1")
    for url in ("http://127.0.0.1:9", "http://169.254.169.254", "file:///etc/passwd"):
        with pytest.raises(ProviderError) as e:
            lc().start("ollama", url, "m")
        assert e.value.kind == "blocked"
    with pytest.raises(ProviderError):
        lc().start("skynet", "http://127.0.0.1:1", "m")
    with pytest.raises(ProviderError):
        lc().start("ollama", "http://127.0.0.1:1", "")                              # a model is required


# ----------------------------------------------------------------- not running
def test_not_running_and_autostart_off_says_how_to_start_it(lc, tmp_path):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    fake_backends.install(tmp_path / "bin", tmp_path / "fake.log")
    os.environ["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    configure(url, autostart=False)
    st = start(lc(), "ollama", url, "llama3.2:3b")
    assert st["state"] == "error" and st["errorKind"] == "not_running" and "ollama serve" in st["hint"]
    assert not (tmp_path / "fake.log.argv").exists()                                # nothing was launched


def test_not_running_and_not_installed(lc):
    url = f"http://127.0.0.1:{free_port()}"
    configure(url)
    st = start(lc(), "ollama", url, "llama3.2:3b")
    assert st["state"] == "error" and st["errorKind"] == "not_installed" and "ollama.com" in st["hint"]


def test_autostart_launches_ollama_serve_then_stop_terminates_it(lc, tmp_path):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    fake_backends.install(tmp_path / "bin", tmp_path / "fake.log")
    os.environ["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    configure(url)
    l = lc()
    st = start(l, "ollama", url, "qwen2.5-coder:7b")
    assert st["state"] == "ready" and st["startedBackend"] is True and st["loadedByUs"] is True
    assert (tmp_path / "fake.log.argv").read_text().split() == ["serve"]            # fixed argv, no shell, no extras
    assert ops(tmp_path, "ollama") == [("load", "qwen2.5-coder:7b")]
    pid = l._proc.pid
    assert pid_alive(pid)
    l.stop()
    deadline = time.time() + 5
    while pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not pid_alive(pid) and l.status()["state"] == "off"


def test_the_backend_log_is_private_and_lives_outside_any_repo(lc, tmp_path):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    fake_backends.install(tmp_path / "bin", tmp_path / "fake.log")
    os.environ["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    configure(url)
    start(lc(), "ollama", url, "llama3.2:3b")
    logs = list((tmp_path / "home" / "logs").glob("*.log"))
    assert logs and (os.stat(logs[0]).st_mode & 0o777) == 0o600


def test_a_backend_that_exits_immediately_reports_its_output(lc, tmp_path):
    url = f"http://127.0.0.1:{free_port()}"
    fake_backends.install(tmp_path / "bin", tmp_path / "fake.log", ollama_script='echo "error: address already in use" >&2\nexit 1\n')
    os.environ["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    configure(url)
    st = start(lc(), "ollama", url, "m")
    assert st["state"] == "error" and st["errorKind"] == "start_failed" and "address already in use" in st["error"]


def test_a_backend_that_never_answers_times_out_and_is_killed(lc, tmp_path):
    url = f"http://127.0.0.1:{free_port()}"
    fake_backends.install(tmp_path / "bin", tmp_path / "fake.log", ollama_script="exec sleep 60\n")
    os.environ["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    configure(url)
    l = lc(start_timeout=1.0)
    st = start(l, "ollama", url, "m")
    assert st["state"] == "error" and st["errorKind"] == "start_timeout"
    assert not pid_alive(l._last_pid)                                                # the stuck process was not left behind


def test_remote_endpoints_are_never_auto_started(lc, tmp_path):
    fake_backends.install(tmp_path / "bin", tmp_path / "fake.log")
    os.environ["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    url = "http://203.0.113.9:11434"
    settings.update({"allow_remote": True, "endpoints": [{"provider": "ollama", "url": url}]})
    st = start(lc(probe_timeout=0.4), "ollama", url, "m")
    assert st["state"] == "error" and st["errorKind"] in ("not_running", "timeout") and not (tmp_path / "fake.log.argv").exists()


def test_stop_never_touches_a_backend_it_did_not_start(lc, servers):
    s = servers(ollama_routes({("POST", "/api/chat"): {"done": True}}))
    configure(s.url)
    l = lc()
    start(l, "ollama", s.url, "qwen2.5-coder:7b")
    l.stop()
    assert OllamaUp(s.url)                                                           # still reachable after stop


def OllamaUp(url):
    from code_explainer.ai.http import request_json
    return bool(request_json(url + "/api/version"))


# ----------------------------------------------------------------- LM Studio
def lms_setup(tmp_path, **env):
    port = free_port()
    fake_backends.install(tmp_path / "bin", tmp_path / "fake.log")
    os.environ["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    for k, v in env.items():
        os.environ[k] = v
    url = f"http://127.0.0.1:{port}"
    configure(url, provider="lmstudio")
    return url


def test_lmstudio_autostart_loads_over_rest_and_stop_unloads_and_stops_the_server(lc, tmp_path, monkeypatch):
    url = lms_setup(tmp_path)
    l = lc()
    st = start(l, "lmstudio", url, "llama-3.2-3b-instruct")
    assert st["state"] == "ready" and st["startedBackend"] is True and st["loadedByUs"] is True
    assert (tmp_path / "fake.log.argv").read_text().split()[:4] == ["server", "start", "--port", url.rsplit(":", 1)[1]]
    assert ops(tmp_path, "lms") == [("load", "llama-3.2-3b-instruct")]
    pid = int((tmp_path / "fake.log.lmspid").read_text())
    l.stop()
    deadline = time.time() + 5
    while pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not pid_alive(pid) and "stop" in (tmp_path / "fake.log.argv").read_text()


def test_lmstudio_model_already_loaded_by_the_user_stays_loaded(lc, tmp_path):
    url = lms_setup(tmp_path, FAKE_PRELOADED="qwen/qwen2.5-coder-7b")
    l = lc()
    st = start(l, "lmstudio", url, "qwen/qwen2.5-coder-7b")
    assert st["state"] == "ready" and st["loadedByUs"] is False
    l.stop()
    assert ops(tmp_path, "lms") == []


def test_lmstudio_without_the_load_endpoint_falls_back_to_the_cli(lc, tmp_path):
    url = lms_setup(tmp_path, FAKE_NO_LOAD_ENDPOINT="1")
    st = start(lc(), "lmstudio", url, "llama-3.2-3b-instruct")
    assert st["state"] == "ready" and st["loadedByUs"] is True
    assert (tmp_path / "fake.log.cliload").read_text().split() == ["load", "llama-3.2-3b-instruct", "--context-length", "4096"]


def test_lmstudio_load_failure_is_an_error_and_cleans_up_what_it_started(lc, tmp_path):
    url = lms_setup(tmp_path, FAKE_LOAD_FAILS="1")
    l = lc()
    st = start(l, "lmstudio", url, "llama-3.2-3b-instruct")
    assert st["state"] == "error" and st["errorKind"] == "load_failed" and "not enough memory" in st["error"]
    pid = int((tmp_path / "fake.log.lmspid").read_text())
    deadline = time.time() + 5
    while pid_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not pid_alive(pid)                                                        # it started the server, so it stops it


def test_lmstudio_not_loadable_by_any_route_still_goes_ready_with_a_warning(lc, tmp_path):
    url = lms_setup(tmp_path, FAKE_NO_LOAD_ENDPOINT="1")
    fake_backends.install(tmp_path / "bin", tmp_path / "fake.log", ollama=False)     # rewrite lms to fail `load`
    (tmp_path / "bin" / "lms").write_text((tmp_path / "bin" / "lms").read_text().replace('if [ "$1" = load ]; then', 'if [ "$1" = load ]; then exit 1; fi\nif false; then'))
    st = start(lc(), "lmstudio", url, "llama-3.2-3b-instruct")
    assert st["state"] == "ready" and st["warnings"] and "just-in-time" in st["warnings"][0].lower()


# ----------------------------------------------------------------- state machine
def test_switching_models_unloads_the_old_one_first(lc, servers):
    s = servers(ollama_routes({("POST", "/api/chat"): {"done": True}}))
    configure(s.url)
    l = lc()
    start(l, "ollama", s.url, "qwen2.5-coder:7b")
    st = start(l, "ollama", s.url, "nomic-embed-text:latest")
    assert st["state"] == "ready" and st["model"] == "nomic-embed-text:latest"
    chats = [(r["body"]["model"], r["body"]["keep_alive"]) for r in s.requests if r["path"] == "/api/chat"]
    assert chats == [("qwen2.5-coder:7b", "10m"), ("qwen2.5-coder:7b", 0), ("nomic-embed-text:latest", "10m")]


def test_starting_the_same_model_twice_is_a_no_op(lc, servers):
    s = servers(ollama_routes({("POST", "/api/chat"): {"done": True}}))
    configure(s.url)
    l = lc()
    start(l, "ollama", s.url, "qwen2.5-coder:7b")
    n = len([r for r in s.requests if r["path"] == "/api/chat"])
    assert start(l, "ollama", s.url, "qwen2.5-coder:7b")["state"] == "ready"
    assert len([r for r in s.requests if r["path"] == "/api/chat"]) == n


def test_stop_while_starting_cancels_and_cleans_up(lc, tmp_path):
    url = f"http://127.0.0.1:{free_port()}"
    fake_backends.install(tmp_path / "bin", tmp_path / "fake.log", ollama_script="exec sleep 60\n")
    os.environ["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    configure(url)
    l = lc(start_timeout=30)
    l.start("ollama", url, "m")
    deadline = time.time() + 5
    while l._proc is None and time.time() < deadline:
        time.sleep(0.02)
    pid = l._proc.pid
    t0 = time.time()
    l.stop()
    l.wait(5)
    assert l.status()["state"] == "off" and time.time() - t0 < 5 and not pid_alive(pid)


def test_stop_and_shutdown_are_idempotent_and_safe_before_anything_started(lc):
    l = lc()
    assert l.stop()["state"] == "off"
    l.shutdown()
    l.shutdown()
    assert l.status()["state"] == "off" and l.status()["warnings"] == []


# ----------------------------------------------------------------- idle timeout
def test_idle_timeout_unloads_and_stops_but_never_during_use(lc, servers):
    s = servers(ollama_routes({("POST", "/api/chat"): {"done": True}}))
    configure(s.url, idle_minutes=2)
    l = lc()
    start(l, "ollama", s.url, "qwen2.5-coder:7b")
    t = time.time()
    assert l.check_idle(now=t + 60) is False and l.status()["state"] == "ready"      # 1 minute: not yet
    l.begin_use()
    assert l.check_idle(now=t + 10_000) is False                                     # busy answering: never
    l.end_use()
    assert l.check_idle(now=t + 60) is False                                         # end_use counts as activity
    assert l.check_idle(now=time.time() + 121) is True
    st = l.status()
    assert st["state"] == "off" and "idle" in " ".join(st["warnings"]).lower()
    assert [r["body"].get("keep_alive") for r in s.requests if r["path"] == "/api/chat"][-1] == 0


def test_status_reports_time_until_idle(lc, servers):
    s = servers(ollama_routes({("POST", "/api/chat"): {"done": True}}))
    configure(s.url, idle_minutes=5)
    l = lc()
    assert l.status()["state"] == "off" and l.status()["secondsToIdle"] is None
    start(l, "ollama", s.url, "qwen2.5-coder:7b")
    assert 280 <= l.status()["secondsToIdle"] <= 300
