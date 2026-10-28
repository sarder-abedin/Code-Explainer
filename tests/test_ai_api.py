import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from ai_fakes import LMS_V1, FakeServer, ollama_routes
from code_explainer.ai import settings as ai_settings
from code_explainer.server import Session, make_handler
from test_server import call

JS = {"Content-Type": "application/json"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_EXPLAINER_HOME", str(tmp_path / "home"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Session(proj, "proj", None, None)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    fakes = []

    def fake(routes):
        f = FakeServer(routes)
        fakes.append(f)
        return f
    yield f"http://127.0.0.1:{httpd.server_port}", fake
    httpd.shutdown()
    httpd.server_close()
    for f in fakes:
        f.stop()


def post(base, path, obj, headers=JS):
    return call(base + path, json.dumps(obj).encode(), headers)


def test_settings_start_as_defaults_and_include_the_ui_schema(api):
    base, _ = api
    code, body = call(base + "/api/ai/settings")
    data = json.loads(body)
    assert code == 200 and data["settings"] == ai_settings.defaults()
    assert "temperature" in data["schema"]["params"] and "presets" in data["schema"]


def test_posting_settings_cleans_clamps_and_persists(api):
    base, _ = api
    code, body = post(base, "/api/ai/settings", {"params": {"temperature": 99, "top_k": "12", "seed": "x"},
                                                  "evil": 1, "idle_minutes": 5})
    s = json.loads(body)["settings"]
    assert code == 200 and s["params"]["temperature"] == 2.0 and s["params"]["top_k"] == 12 and s["idle_minutes"] == 5
    assert "evil" not in s and s["params"]["seed"] is None
    assert json.loads(call(base + "/api/ai/settings")[1])["settings"] == s          # persisted
    assert ai_settings.load() == s                                                   # and on disk


def test_a_partial_update_keeps_earlier_settings(api):
    base, _ = api
    post(base, "/api/ai/settings", {"params": {"temperature": 0.9}})
    s = json.loads(post(base, "/api/ai/settings", {"autostart": False})[1])["settings"]
    assert s["params"]["temperature"] == 0.9 and s["autostart"] is False


def test_remote_endpoints_are_refused_unless_opted_in(api):
    base, _ = api
    remote = {"provider": "openai", "url": "http://my-box.lan:1234"}
    s = json.loads(post(base, "/api/ai/settings", {"endpoints": [remote]})[1])["settings"]
    assert s["endpoints"] == []
    s = json.loads(post(base, "/api/ai/settings", {"allow_remote": True, "endpoints": [remote]})[1])["settings"]
    assert len(s["endpoints"]) == 1


@pytest.mark.parametrize("path", ["/api/ai/settings"])
def test_post_protections_match_the_progress_api(api, path):
    base, _ = api
    assert call(base + path, b"{}", {"Content-Type": "text/plain"})[0] == 415
    assert call(base + path, b"not json", JS)[0] == 400
    assert call(base + path, b"{}", {**JS, "Origin": "http://evil.example"})[0] == 403
    assert call(base + path, b"{}", {**JS, "Host": "evil.example"})[0] == 403
    # An oversized body is refused without being read, so the client may see a 413 or a dropped connection.
    try:
        assert call(base + path, b"x" * 2_000_000, JS)[0] == 413
    except OSError:
        pass
    assert json.loads(call(base + path)[1])["settings"] == ai_settings.defaults()   # nothing was saved


def test_get_endpoints_reject_foreign_hosts(api):
    base, _ = api
    for path in ("/api/ai/settings", "/api/ai/providers"):
        assert call(base + path, headers={"Host": "evil.example"})[0] == 403


def test_providers_lists_configured_servers_and_their_models(api):
    base, fake = api
    o, l = fake(ollama_routes()), fake({("GET", "/api/v1/models"): LMS_V1})
    post(base, "/api/ai/settings", {"endpoints": [{"provider": "ollama", "url": o.url, "name": "mine"},
                                                  {"provider": "lmstudio", "url": l.url}]})
    code, body = call(base + "/api/ai/providers")
    data = json.loads(body)
    assert code == 200
    mine = [p for p in data["providers"] if p["endpoint"] in (o.url, l.url)]
    assert {p["provider"] for p in mine} == {"ollama", "lmstudio"} and all(p["reachable"] for p in mine)
    ollama = next(p for p in mine if p["provider"] == "ollama")
    assert ollama["name"] == "mine" and "llama3.2:3b" in {m["id"] for m in ollama["models"]}
    assert data["selected"] == {"provider": "", "endpoint": "", "model": ""}


def test_providers_never_leaks_secrets(api, monkeypatch):
    base, fake = api
    monkeypatch.setenv("SECRET_KEY_VAR", "sk-very-secret")
    g = fake({("GET", "/v1/models"): {"data": [{"id": "m"}]}})
    post(base, "/api/ai/settings", {"endpoints": [{"provider": "openai", "url": g.url, "api_key_env": "SECRET_KEY_VAR"}]})
    for path in ("/api/ai/providers", "/api/ai/settings"):
        assert "sk-very-secret" not in call(base + path)[1].decode()
    assert g.requests[0]["headers"]["Authorization"] == "Bearer sk-very-secret"      # but the server did receive it


def test_selected_model_is_remembered(api):
    base, fake = api
    o = fake(ollama_routes())
    sel = {"provider": "ollama", "endpoint": o.url, "model": "qwen2.5-coder:7b"}
    post(base, "/api/ai/settings", {"endpoints": [{"provider": "ollama", "url": o.url}], "selected": sel})
    assert json.loads(call(base + "/api/ai/providers")[1])["selected"] == sel


# ======================================================================= step 2: lifecycle + chat over HTTP
import time
import urllib.request

from ai_fakes import ndjson_route, ollama_tokens


def sse_events(base, path, obj, headers=JS, timeout=20):
    """POST and read a server-sent-events stream to its end. Returns (status, content_type, events)."""
    req = urllib.request.Request(base + path, json.dumps(obj).encode(), headers, method="POST")
    events = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode().strip()
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
        return r.status, r.headers.get("Content-Type"), events


def chat_fake(fake, route):
    def chat(handler, body):
        if body.get("messages") == []:
            return handler._send(200, {"model": body.get("model"), "done": True})
        return route(handler, body)
    return fake(ollama_routes({("POST", "/api/chat"): chat}))


def start_ai(base, url, model="qwen2.5-coder:7b", wait=10):
    post(base, "/api/ai/settings", {"endpoints": [{"provider": "ollama", "url": url}], "autostart": False})
    code, body = post(base, "/api/ai/start", {"provider": "ollama", "endpoint": url, "model": model})
    assert code == 200, body
    deadline = time.time() + wait
    while time.time() < deadline:
        st = json.loads(call(base + "/api/ai/status")[1])
        if st["state"] in ("ready", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("never became ready")


def test_status_is_off_until_started(api):
    base, _ = api
    code, body = call(base + "/api/ai/status")
    st = json.loads(body)
    assert code == 200 and st["state"] == "off" and st["secondsToIdle"] is None and st["warnings"] == []


def test_start_remembers_the_selection_and_reaches_ready(api):
    base, fake = api
    s = chat_fake(fake, ndjson_route(ollama_tokens("hi")))
    st = start_ai(base, s.url)
    assert st["state"] == "ready" and st["model"] == "qwen2.5-coder:7b" and st["loadedByUs"] is True
    sel = ai_settings.load()["selected"]
    assert sel == {"provider": "ollama", "endpoint": s.url, "model": "qwen2.5-coder:7b"}


@pytest.mark.parametrize("body", [
    {"provider": "ollama", "endpoint": "http://169.254.169.254", "model": "m"},     # not a configured endpoint
    {"provider": "ollama", "endpoint": "file:///etc/passwd", "model": "m"},
    {"provider": "skynet", "endpoint": "http://127.0.0.1:11434", "model": "m"},
    {"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model": ""},
    {}, [], "x",
])
def test_start_rejects_anything_that_is_not_a_known_endpoint_and_model(api, body):
    base, _ = api
    code, resp = post(base, "/api/ai/start", body)
    assert code == 400 and json.loads(resp)["kind"] == "blocked"
    assert json.loads(call(base + "/api/ai/status")[1])["state"] == "off"


def test_start_with_an_error_state_reports_a_hint(api):
    base, fake = api
    s = chat_fake(fake, ndjson_route(ollama_tokens("hi")))
    st = start_ai(base, s.url, model="mystery:1b")
    assert st["state"] == "error" and st["errorKind"] == "model_missing" and "ollama pull" in st["hint"]


def test_chat_streams_server_sent_events(api):
    base, fake = api
    s = chat_fake(fake, ndjson_route(ollama_tokens("Hel", "lo")))
    start_ai(base, s.url)
    status, ctype, ev = sse_events(base, "/api/ai/chat", {"messages": [{"role": "user", "content": "hi"}]})
    assert status == 200 and ctype.startswith("text/event-stream")
    assert [e["type"] for e in ev] == ["start", "token", "token", "done"]
    assert "".join(e["text"] for e in ev if e["type"] == "token") == "Hello"
    assert ev[0]["messages"][0]["role"] == "system" and ev[0]["model"] == "qwen2.5-coder:7b"


def test_chat_before_start_is_a_clear_event_not_a_crash(api):
    base, _ = api
    status, _, ev = sse_events(base, "/api/ai/chat", {"messages": [{"role": "user", "content": "hi"}]})
    assert status == 200 and [e["type"] for e in ev] == ["error"] and ev[0]["kind"] == "not_ready"


def wait_until(predicate, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def status(base):
    return json.loads(call(base + "/api/ai/status")[1])


def test_the_stop_button_cancels_an_answer_in_progress(api):
    base, fake = api
    s = chat_fake(fake, ndjson_route(ollama_tokens(*["w"] * 300), delay=0.05))
    start_ai(base, s.url)
    got = []
    t = threading.Thread(target=lambda: got.extend(
        sse_events(base, "/api/ai/chat", {"messages": [{"role": "user", "content": "hi"}]})[2]))
    t.start()
    assert wait_until(lambda: status(base)["activeIds"])
    code, body = post(base, "/api/ai/cancel", {"id": status(base)["activeIds"][0]})
    assert code == 200 and json.loads(body) == {"cancelled": True}
    t.join(10)
    assert not t.is_alive() and got[-1]["type"] == "cancelled" and len(got) < 250
    assert json.loads(post(base, "/api/ai/cancel", {"id": "nope"})[1]) == {"cancelled": False}


def test_closing_the_browser_tab_stops_the_model(api):
    base, fake = api
    s = chat_fake(fake, ndjson_route(ollama_tokens(*["w"] * 500), delay=0.03))
    start_ai(base, s.url)
    req = urllib.request.Request(base + "/api/ai/chat", json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode(),
                                 JS, method="POST")
    r = urllib.request.urlopen(req, timeout=10)
    r.readline()
    r.readline()
    t0 = time.time()
    r.close()                                                                         # the tab went away mid-answer
    assert wait_until(lambda: not status(base)["busy"], 3) and time.time() - t0 < 3   # well before the 15 s answer would end
    r2 = urllib.request.urlopen(urllib.request.Request(
        base + "/api/ai/chat", json.dumps({"messages": [{"role": "user", "content": "again"}]}).encode(), JS, method="POST"), timeout=10)
    first = r2.readline().decode()
    r2.close()
    assert '"type": "start"' in first                                                 # the slot was released, not "busy"


def test_stop_unloads_the_model_and_goes_off(api):
    base, fake = api
    s = chat_fake(fake, ndjson_route(ollama_tokens("hi")))
    start_ai(base, s.url)
    code, body = post(base, "/api/ai/stop", {})
    assert code == 200 and json.loads(body)["state"] == "off"
    assert [r["body"].get("keep_alive") for r in s.requests if r["path"] == "/api/chat"][-1] == 0


def test_new_endpoints_have_the_same_protections(api):
    base, _ = api
    for path in ("/api/ai/start", "/api/ai/stop", "/api/ai/chat", "/api/ai/cancel"):
        assert call(base + path, b"{}", {"Content-Type": "text/plain"})[0] == 415, path
        assert call(base + path, b"not json", JS)[0] == 400, path
        assert call(base + path, b"{}", {**JS, "Origin": "http://evil.example"})[0] == 403, path
        assert call(base + path, b"{}", {**JS, "Host": "evil.example"})[0] == 403, path
    assert call(base + "/api/ai/status", headers={"Host": "evil.example"})[0] == 403


def test_chat_input_is_validated_before_anything_is_sent(api):
    base, fake = api
    s = chat_fake(fake, ndjson_route(ollama_tokens("hi")))
    start_ai(base, s.url)
    _, _, ev = sse_events(base, "/api/ai/chat", {"messages": "not a list"})
    assert ev[0]["kind"] == "bad_request"
    _, _, ev = sse_events(base, "/api/ai/chat", {"messages": [{"role": "system", "content": "x"}]})
    assert ev[0]["kind"] == "bad_request"


def test_shutting_the_server_down_unloads_the_model_it_loaded(tmp_path, monkeypatch):
    import code_explainer.server as srv
    monkeypatch.setenv("CODE_EXPLAINER_HOME", str(tmp_path / "home"))
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n")
    f = FakeServer(ollama_routes({("POST", "/api/chat"): {"done": True}}))
    ai_settings.update({"endpoints": [{"provider": "ollama", "url": f.url}], "autostart": False})
    result = {}

    def ready(httpd, session):
        def go():
            session.ai.lifecycle.start("ollama", f.url, "qwen2.5-coder:7b")
            result["state"] = session.ai.lifecycle.wait(10)["state"]
            httpd.shutdown()
        threading.Thread(target=go, daemon=True).start()

    try:
        srv.serve(proj, "p", port=0, watch=False, on_ready=ready)
        assert result["state"] == "ready"
        chats = [r["body"] for r in f.requests if r["path"] == "/api/chat"]
        assert chats[0]["keep_alive"] == "10m" and chats[-1] == {"model": "qwen2.5-coder:7b", "messages": [], "keep_alive": 0}
    finally:
        f.stop()


def test_runtime_shutdown_cancels_an_answer_in_progress(api):
    base, fake = api
    s = chat_fake(fake, ndjson_route(ollama_tokens(*["w"] * 300), delay=0.05))
    start_ai(base, s.url)
    got = []
    t = threading.Thread(target=lambda: got.extend(
        sse_events(base, "/api/ai/chat", {"messages": [{"role": "user", "content": "hi"}]})[2]))
    t.start()
    assert wait_until(lambda: status(base)["activeIds"])
    code, _ = post(base, "/api/ai/stop", {})
    t.join(10)
    assert code == 200 and not t.is_alive() and got[-1]["type"] in ("cancelled", "error")
    assert status(base)["state"] == "off"


def test_switching_models_cancels_the_answer_in_progress(api):
    base, fake = api
    s = chat_fake(fake, ndjson_route(ollama_tokens(*["w"] * 300), delay=0.05))
    start_ai(base, s.url)
    got = []
    t = threading.Thread(target=lambda: got.extend(
        sse_events(base, "/api/ai/chat", {"messages": [{"role": "user", "content": "hi"}]})[2]))
    t.start()
    assert wait_until(lambda: status(base)["activeIds"])
    code, _ = post(base, "/api/ai/start", {"provider": "ollama", "endpoint": s.url, "model": "llama3.2:3b"})
    t.join(10)
    assert code == 200 and not t.is_alive() and got[-1]["type"] in ("cancelled", "error")
    assert wait_until(lambda: status(base)["state"] == "ready" and status(base)["model"] == "llama3.2:3b", 10)
