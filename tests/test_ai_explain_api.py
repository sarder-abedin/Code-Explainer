import json
import threading
from http.server import ThreadingHTTPServer

from ai_fakes import FakeServer, ndjson_route, ollama_tokens
from code_explainer.ai import settings as ai_settings
from code_explainer.server import Session, make_handler
from test_ai_api import JS, call, chat_fake, post, shop_api, sse_events, start_ai  # noqa: F401  (shared fixtures/helpers)

SYMBOL = {"kind": "symbol", "path": "shop/pricing.py", "symbol": "total"}


def explain(base, **req):
    return sse_events(base, "/api/ai/explain", {**SYMBOL, **req})


def model_calls(f):
    return [r for r in f.requests if r["path"] == "/api/chat" and r["body"]["messages"]]


def test_explain_streams_then_serves_the_saved_answer(shop_api):
    base, fake, session, proj = shop_api
    f = chat_fake(fake, ndjson_route(ollama_tokens("It ", "adds.")))
    start_ai(base, f.url)
    status, ctype, ev = explain(base)
    assert status == 200 and ctype.startswith("text/event-stream")
    assert [e["type"] for e in ev] == ["start", "token", "token", "done"] and ev[0]["cached"] is False
    assert ev[0]["sources"][0]["label"] == "total" and "sum(i.price" in ev[0]["messages"][0]["content"]
    _, _, again = explain(base)
    assert again[0]["cached"] is True and [e["type"] for e in again] == ["start", "token", "done"]
    assert "".join(e["text"] for e in again if e["type"] == "token") == "It adds." and len(model_calls(f)) == 1
    _, _, regen = explain(base, regenerate=True)
    assert regen[0]["cached"] is False and len(model_calls(f)) == 2


def test_a_saved_answer_is_available_after_the_ai_is_stopped(shop_api):
    base, fake, session, proj = shop_api
    f = chat_fake(fake, ndjson_route(ollama_tokens("Saved.")))
    start_ai(base, f.url)
    explain(base)
    post(base, "/api/ai/stop", {})
    ev = explain(base)[2]
    assert ev[0]["cached"] is True and ev[-1]["type"] == "done"
    other = explain(base, symbol="nonexistent")[2]
    assert other[0]["type"] == "error" and other[0]["kind"] == "bad_request"


def test_not_ready_without_a_saved_answer(shop_api):
    base, fake, session, proj = shop_api
    ev = explain(base)[2]
    assert [e["type"] for e in ev] == ["error"] and ev[0]["kind"] == "not_ready"


def test_the_cache_can_be_inspected_and_cleared(shop_api):
    base, fake, session, proj = shop_api
    f = chat_fake(fake, ndjson_route(ollama_tokens("x")))
    start_ai(base, f.url)
    assert json.loads(call(base + "/api/ai/cache")[1]) == {"entries": 0, "bytes": 0}
    explain(base)
    explain(base, kind="file", path="shop/pricing.py", symbol=None)
    st = json.loads(call(base + "/api/ai/cache")[1])
    assert st["entries"] == 2 and st["bytes"] > 0
    code, body = post(base, "/api/ai/cache/clear", {})
    assert code == 200 and json.loads(body) == {"cleared": 2}
    assert json.loads(call(base + "/api/ai/cache")[1]) == {"entries": 0, "bytes": 0}
    assert explain(base)[2][0]["cached"] is False


def test_the_cache_lives_outside_the_project_and_is_private(shop_api, tmp_path):
    import os
    base, fake, session, proj = shop_api
    f = chat_fake(fake, ndjson_route(ollama_tokens("x")))
    start_ai(base, f.url)
    explain(base)
    assert not [p for p in proj.rglob("*") if "cache" in p.name.lower() and p.suffix == ".json"]       # nothing added to the repo
    files = list((tmp_path / "home" / "ai-cache").rglob("*.json"))
    assert len(files) == 1 and (os.stat(files[0]).st_mode & 0o777) == 0o600
    assert "sum(i.price" not in files[0].read_text()                                                   # the stored entry holds no source code


def test_new_endpoints_have_the_same_protections(shop_api):
    base, fake, session, proj = shop_api
    for path in ("/api/ai/explain", "/api/ai/cache/clear"):
        assert call(base + path, b"{}", {"Content-Type": "text/plain"})[0] == 415, path
        assert call(base + path, b"not json", JS)[0] == 400, path
        assert call(base + path, b"{}", {**JS, "Origin": "http://evil.example"})[0] == 403, path
        assert call(base + path, b"{}", {**JS, "Host": "evil.example"})[0] == 403, path
    assert call(base + "/api/ai/cache", headers={"Host": "evil.example"})[0] == 403


def test_a_change_review_can_be_explained(repo, tmp_path):
    session = Session(repo, "r", "HEAD~1", "HEAD")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    f = FakeServer({})
    try:
        f.stop()
        from ai_fakes import ollama_routes
        f = FakeServer(ollama_routes({("POST", "/api/chat"): lambda h, b: (h._send(200, {"done": True}) if b.get("messages") == [] else
                                                                           ndjson_route(ollama_tokens("It changed load."))(h, b))}))
        ai_settings.update({"endpoints": [{"provider": "ollama", "url": f.url}], "autostart": False})
        session.ai.lifecycle.start("ollama", f.url, "qwen2.5-coder:7b")
        assert session.ai.lifecycle.wait(10)["state"] == "ready"
        ev = sse_events(base, "/api/ai/explain", {"kind": "change", "path": "app/lib.py"})[2]
        assert ev[0]["kind"] == "change" and "@@" in ev[0]["messages"][0]["content"] and "+def fresh" in ev[0]["messages"][0]["content"]
        assert ev[-1]["type"] == "done"
        bad = sse_events(base, "/api/ai/explain", {"kind": "change", "path": "tests/none.py"})[2]
        assert bad[0]["type"] == "error" and bad[0]["kind"] == "bad_request"
    finally:
        session.ai.shutdown()
        httpd.shutdown()
        httpd.server_close()
        f.stop()
