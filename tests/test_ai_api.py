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
