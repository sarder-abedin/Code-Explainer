import pytest

from ai_fakes import (LMS_V0, LMS_V1, OPENAI_MODELS, FakeServer, free_port, ollama_routes, slow)
from code_explainer.ai import http
from code_explainer.ai.http import ProviderError, check_endpoint_url, request_json
from code_explainer.ai.lmstudio import LMStudio
from code_explainer.ai.ollama import Ollama
from code_explainer.ai.openai_compat import OpenAICompat


@pytest.fixture
def serve():
    started = []

    def make(routes):
        s = FakeServer(routes)
        started.append(s)
        return s
    yield make
    for s in started:
        s.stop()


# ----------------------------------------------------------------- URL policy
@pytest.mark.parametrize("url, expected", [
    ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
    ("http://localhost:1234", "http://localhost:1234"),
    ("http://[::1]:8080/v1/", "http://[::1]:8080/v1"),
    ("  HTTP://127.0.0.1:5000  ", "http://127.0.0.1:5000"),
])
def test_loopback_urls_are_accepted_and_normalised(url, expected):
    assert check_endpoint_url(url) == expected


@pytest.mark.parametrize("url", [
    "ftp://127.0.0.1", "file:///etc/passwd", "http://user:pw@127.0.0.1:1234", "http://192.168.1.5:1234",
    "http://example.com", "http://localhost.evil.com", "http://127.0.0.1:99999", "http://127.0.0.1/?a=1",
    "http://", "not a url", "",
])
def test_unsafe_or_malformed_urls_are_blocked(url):
    with pytest.raises(ProviderError) as e:
        check_endpoint_url(url)
    assert e.value.kind == "blocked"


def test_allow_remote_opens_hosts_but_never_credentials():
    assert check_endpoint_url("http://my-box.lan:1234", allow_remote=True) == "http://my-box.lan:1234"
    with pytest.raises(ProviderError):
        check_endpoint_url("http://u:p@my-box.lan", allow_remote=True)
    with pytest.raises(ProviderError):
        check_endpoint_url("gopher://my-box.lan", allow_remote=True)


# ----------------------------------------------------------------- HTTP client
def test_request_json_success_and_error_kinds(serve):
    s = serve({("GET", "/ok"): {"a": 1}, ("GET", "/bad"): (200, b"<html>not json</html>"), ("GET", "/boom"): (500, {"error": "x"}),
               ("GET", "/redir"): (302, b"", {"Location": "http://example.com/"}), ("GET", "/slow"): slow(1.5)})
    assert request_json(s.url + "/ok") == {"a": 1}
    with pytest.raises(ProviderError) as e:
        request_json(s.url + "/missing")
    assert e.value.kind == "http" and e.value.status == 404
    with pytest.raises(ProviderError) as e:
        request_json(s.url + "/boom")
    assert e.value.status == 500
    with pytest.raises(ProviderError) as e:
        request_json(s.url + "/bad")
    assert e.value.kind == "bad_response"
    with pytest.raises(ProviderError) as e:
        request_json(s.url + "/redir")
    assert e.value.kind == "http" and e.value.status == 302      # redirects are never followed
    with pytest.raises(ProviderError) as e:
        request_json(s.url + "/slow", timeout=0.3)
    assert e.value.kind == "timeout"


def test_unreachable_and_oversized(serve, monkeypatch):
    with pytest.raises(ProviderError) as e:
        request_json(f"http://127.0.0.1:{free_port()}/x", timeout=1)
    assert e.value.kind == "unreachable"
    s = serve({("GET", "/big"): {"x": "y" * 500}})
    monkeypatch.setattr(http, "MAX_RESPONSE_BYTES", 100)
    with pytest.raises(ProviderError) as e:
        request_json(s.url + "/big")
    assert e.value.kind == "bad_response" and "too large" in str(e.value)


def test_system_proxy_settings_are_ignored(serve, monkeypatch):
    for k in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(k, f"http://127.0.0.1:{free_port()}")  # a dead proxy would break the request
    s = serve({("GET", "/ok"): {"ok": True}})
    assert request_json(s.url + "/ok") == {"ok": True}


# ----------------------------------------------------------------- Ollama
def test_ollama_lists_downloaded_models_with_loaded_state(serve):
    s = serve(ollama_routes())
    p = Ollama(s.url)
    assert p.version() == "0.5.7"
    models = {m.id: m for m in p.list_models()}
    assert set(models) == {"llama3.2:3b", "nomic-embed-text:latest", "qwen2.5-coder:7b"}
    llama = models["llama3.2:3b"]
    assert (llama.params, llama.quant, llama.family, llama.size_bytes) == ("3.2B", "Q4_K_M", "llama", 2019393189)
    assert llama.loaded is True and models["qwen2.5-coder:7b"].loaded is False
    assert llama.kind == "llm" and models["nomic-embed-text:latest"].kind == "embedding"
    assert llama.provider == "ollama" and llama.endpoint == s.url


def test_ollama_tolerates_ps_failure_and_reads_context_length(serve):
    s = serve(ollama_routes({("GET", "/api/ps"): (500, {"error": "x"})}))
    p = Ollama(s.url)
    assert all(m.loaded is None for m in p.list_models())      # unknown, not "false"
    assert p.context_length("llama3.2:3b") == 131072
    assert s.requests[-1]["body"] == {"model": "llama3.2:3b"}


def test_ollama_unreachable_and_garbage(serve):
    with pytest.raises(ProviderError) as e:
        Ollama(f"http://127.0.0.1:{free_port()}").list_models()
    assert e.value.kind == "unreachable"
    s = serve({("GET", "/api/tags"): {"unexpected": True}})
    assert Ollama(s.url).list_models() == []                  # a missing "models" key means none, not a crash
    s2 = serve({("GET", "/api/tags"): [1, 2]})
    with pytest.raises(ProviderError) as e:
        Ollama(s2.url).list_models()
    assert e.value.kind == "bad_response"


# ----------------------------------------------------------------- LM Studio
def test_lmstudio_native_v1_models(serve):
    s = serve({("GET", "/api/v1/models"): LMS_V1})
    models = {m.id: m for m in LMStudio(s.url).list_models()}
    q = models["qwen/qwen2.5-coder-7b"]
    assert (q.name, q.params, q.quant, q.family, q.context_max, q.size_bytes) == (
        "Qwen2.5 Coder 7B", "7B", "Q4_K_M", "qwen2", 32768, 4683087332)
    assert q.loaded is True and models["llama-3.2-3b-instruct"].loaded is False
    assert models["text-embedding-nomic-embed-text-v1.5"].kind == "embedding"
    assert [r["path"] for r in s.requests] == ["/api/v1/models"]


def test_lmstudio_falls_back_to_v0_then_openai_compat(serve):
    s0 = serve({("GET", "/api/v0/models"): LMS_V0})
    m = {x.id: x for x in LMStudio(s0.url).list_models()}
    assert m["qwen2.5-coder-7b"].loaded is True and m["llama-3.2-3b"].loaded is False
    assert m["llama-3.2-3b"].kind == "llm" and m["text-embedding-x"].kind == "embedding"
    s1 = serve({("GET", "/v1/models"): OPENAI_MODELS})
    m = LMStudio(s1.url).list_models()
    assert [x.id for x in m] == ["my-chat-model", "bge-small-embed"] and all(x.loaded is None for x in m)
    assert [r["path"] for r in s1.requests] == ["/api/v1/models", "/api/v0/models", "/v1/models"]


def test_lmstudio_sends_api_token_and_reports_real_errors(serve):
    s = serve({("GET", "/api/v1/models"): LMS_V1})
    LMStudio(s.url, api_key="sekret").list_models()
    assert s.requests[0]["headers"]["Authorization"] == "Bearer sekret"
    s401 = serve({("GET", "/api/v1/models"): (401, {"error": "bad token"}), ("GET", "/api/v0/models"): (401, {}),
                  ("GET", "/v1/models"): (401, {"error": "bad token"})})
    with pytest.raises(ProviderError) as e:
        LMStudio(s401.url).list_models()
    assert e.value.status == 401
    with pytest.raises(ProviderError) as e:
        LMStudio(f"http://127.0.0.1:{free_port()}").list_models()
    assert e.value.kind == "unreachable"                       # does not try the other paths when nothing is listening


# ----------------------------------------------------------------- generic OpenAI-compatible
def test_openai_compatible_models_and_auth(serve):
    s = serve({("GET", "/v1/models"): OPENAI_MODELS})
    p = OpenAICompat(s.url, api_key="k")
    ms = {m.id: m for m in p.list_models()}
    assert ms["my-chat-model"].kind == "llm" and ms["bge-small-embed"].kind == "embedding"
    assert ms["my-chat-model"].provider == "openai" and ms["my-chat-model"].loaded is None
    assert s.requests[0]["headers"]["Authorization"] == "Bearer k"


def test_openai_compat_base_url_may_already_include_v1(serve):
    s = serve({("GET", "/v1/models"): OPENAI_MODELS})
    assert [m.id for m in OpenAICompat(s.url + "/v1").list_models()] == ["my-chat-model", "bge-small-embed"]
