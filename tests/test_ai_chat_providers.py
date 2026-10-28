import threading

import pytest

from ai_fakes import FakeServer, ndjson_route, ollama_tokens, openai_tokens, sse_route
from code_explainer.ai.http import ProviderError
from code_explainer.ai.lmstudio import LMStudio
from code_explainer.ai.ollama import Ollama
from code_explainer.ai.openai_compat import OpenAICompat
from code_explainer.ai.params import GenParams, to_provider_options
from code_explainer.ai.stream import Cancelled

MSGS = [{"role": "system", "content": "be nice"}, {"role": "user", "content": "hi"}]


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


def run(provider, model="m", options=None, cancel=None, **kw):
    return [e for e in provider.stream_chat(model, MSGS, options or {}, cancel or threading.Event(), **kw) if e["type"] != "tick"]


def text_of(events):
    return "".join(e["text"] for e in events if e["type"] == "token")


# ----------------------------------------------------------------- Ollama
def test_ollama_streams_tokens_and_reports_stats(serve):
    s = serve({("POST", "/api/chat"): ndjson_route(ollama_tokens("Hel", "lo ", "wörld"))})
    ev = run(Ollama(s.url), options={"temperature": 0.1, "num_predict": 50, "num_ctx": 2048}, keep_alive="10m")
    assert text_of(ev) == "Hello wörld"
    done = ev[-1]
    assert done["type"] == "done" and done["truncated"] is False
    assert done["stats"] == {"prompt_tokens": 12, "completion_tokens": 3, "tokens_per_second": 6.0}
    body = s.requests[0]["body"]
    assert body == {"model": "m", "messages": MSGS, "stream": True, "keep_alive": "10m",
                    "options": {"temperature": 0.1, "num_predict": 50, "num_ctx": 2048}}


def test_ollama_flags_an_answer_cut_off_by_the_length_limit(serve):
    s = serve({("POST", "/api/chat"): ndjson_route(ollama_tokens("abc", done_reason="length"))})
    assert run(Ollama(s.url))[-1]["truncated"] is True


def test_ollama_thinking_and_garbage_lines_do_not_leak_into_the_answer(serve):
    chunks = [{"message": {"role": "assistant", "content": "", "thinking": "hmm"}, "done": False}, "not json\n", "\n",
              {"message": {"role": "assistant", "content": "ok"}, "done": False}] + ollama_tokens()[-1:]
    s = serve({("POST", "/api/chat"): ndjson_route(chunks)})
    ev = run(Ollama(s.url))
    assert text_of(ev) == "ok" and ev[0] == {"type": "thinking"} and ev[-1]["type"] == "done"


def test_ollama_error_in_the_stream_becomes_a_provider_error(serve):
    s = serve({("POST", "/api/chat"): ndjson_route([{"message": {"content": "partial"}, "done": False},
                                                    {"error": "llama runner process has terminated"}])})
    got = []
    with pytest.raises(ProviderError) as e:
        for ev in Ollama(s.url).stream_chat("m", MSGS, {}, threading.Event()):
            got.append(ev)
    assert "terminated" in str(e.value) and got[0]["text"] == "partial"


def test_ollama_http_error_for_a_missing_model(serve):
    s = serve({("POST", "/api/chat"): (404, {"error": "model \"nope\" not found, try pulling it first"})})
    with pytest.raises(ProviderError) as e:
        run(Ollama(s.url), model="nope")
    assert e.value.status == 404 and "try pulling it first" in str(e.value)


def test_ollama_connection_dropped_before_the_answer_finished(serve):
    s = serve({("POST", "/api/chat"): ndjson_route([{"message": {"content": "half"}, "done": False}])})
    with pytest.raises(ProviderError) as e:
        run(Ollama(s.url))
    assert e.value.kind == "bad_response" and "closed" in str(e.value)


def test_ollama_empty_answer_is_an_error_not_silence(serve):
    s = serve({("POST", "/api/chat"): ndjson_route([])})
    with pytest.raises(ProviderError):
        run(Ollama(s.url))


def test_ollama_cancel(serve):
    s = serve({("POST", "/api/chat"): ndjson_route(ollama_tokens(*["x"] * 50), delay=0.05)})
    cancel = threading.Event()
    got = []
    with pytest.raises(Cancelled):
        for ev in Ollama(s.url).stream_chat("m", MSGS, {}, cancel):
            got.append(ev)
            if len(got) == 3:
                cancel.set()
    assert 3 <= len(got) < 20


def test_ollama_load_and_unload_use_an_empty_chat(serve):
    s = serve({("POST", "/api/chat"): {"model": "m", "done": True, "done_reason": "load"}})
    p = Ollama(s.url)
    assert p.supports_load
    p.load("m", {"num_ctx": 8192}, keep_alive="15m")
    p.unload("m")
    assert s.requests[0]["body"] == {"model": "m", "messages": [], "keep_alive": "15m", "options": {"num_ctx": 8192}}
    assert s.requests[1]["body"] == {"model": "m", "messages": [], "keep_alive": 0}


# ----------------------------------------------------------------- OpenAI-compatible / LM Studio
def test_openai_sse_streaming_and_request_shape(serve):
    s = serve({("POST", "/v1/chat/completions"): sse_route(openai_tokens("Hel", "lo"))})
    opts = to_provider_options("openai", GenParams(seed=3))
    ev = run(OpenAICompat(s.url, api_key="k"), model="gpt-x", options=opts)
    assert text_of(ev) == "Hello" and ev[-1]["type"] == "done" and ev[-1]["truncated"] is False
    body, headers = s.requests[0]["body"], s.requests[0]["headers"]
    assert body == {"model": "gpt-x", "messages": MSGS, "stream": True, "temperature": 0.2, "top_p": 0.9,
                    "max_tokens": 600, "seed": 3}
    assert headers["Authorization"] == "Bearer k"


def test_openai_base_url_with_v1_and_lmstudio_extras(serve):
    s = serve({("POST", "/v1/chat/completions"): sse_route(openai_tokens("ok"))})
    run(LMStudio(s.url + "/v1"), options=to_provider_options("lmstudio", GenParams()))
    body = s.requests[0]["body"]
    assert body["top_k"] == 40 and body["repeat_penalty"] == 1.1 and "context_size" not in body and "min_p" not in body


def test_openai_ignores_comments_events_reasoning_and_finish_length(serve):
    chunks = [": keep-alive\n\n", "event: ping\n\n", {"choices": [{"delta": {"reasoning_content": "hmm"}}]},
              {"choices": [{"delta": {"content": "ans"}}]}, {"choices": []},
              {"choices": [{"delta": {}, "finish_reason": "length"}]}, "data: [DONE]\n\n"]
    s = serve({("POST", "/v1/chat/completions"): sse_route(chunks)})
    ev = run(OpenAICompat(s.url))
    assert text_of(ev) == "ans" and {"type": "thinking"} in ev and ev[-1]["truncated"] is True


def test_openai_finish_without_done_marker_is_still_complete(serve):
    chunks = [{"choices": [{"delta": {"content": "a"}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
    s = serve({("POST", "/v1/chat/completions"): sse_route(chunks)})
    assert run(OpenAICompat(s.url))[-1]["type"] == "done"


def test_openai_dropped_connection_and_error_chunks(serve):
    s = serve({("POST", "/v1/chat/completions"): sse_route([{"choices": [{"delta": {"content": "a"}}]}])})
    with pytest.raises(ProviderError) as e:
        run(OpenAICompat(s.url))
    assert "closed" in str(e.value)
    s2 = serve({("POST", "/v1/chat/completions"): sse_route([{"error": {"message": "context length exceeded"}}])})
    with pytest.raises(ProviderError) as e:
        run(OpenAICompat(s2.url))
    assert "context length exceeded" in str(e.value)


def test_openai_http_errors(serve):
    s = serve({("POST", "/v1/chat/completions"): (401, {"error": {"message": "bad key"}})})
    with pytest.raises(ProviderError) as e:
        run(OpenAICompat(s.url))
    assert e.value.status == 401 and "bad key" in str(e.value)


def test_lmstudio_load_uses_the_documented_rest_endpoint(serve):
    s = serve({("POST", "/api/v1/models/load"): {"type": "llm", "instance_id": "qwen/q-7b", "load_time_seconds": 3.2, "status": "loaded"},
               ("POST", "/api/v1/models/unload"): {"instance_id": "qwen/q-7b"}})
    p = LMStudio(s.url, api_key="tok")
    assert p.supports_load
    assert p.load("qwen/q-7b", {"context_size": 8192}) == "qwen/q-7b"
    p.unload("qwen/q-7b")
    assert s.requests[0]["body"] == {"model": "qwen/q-7b", "context_length": 8192}
    assert s.requests[1]["body"] == {"instance_id": "qwen/q-7b"}
    assert s.requests[0]["headers"]["Authorization"] == "Bearer tok"


def test_lmstudio_load_on_an_older_server_is_reported_as_unsupported(serve):
    s = serve({})
    with pytest.raises(ProviderError) as e:
        LMStudio(s.url).load("m", {})
    assert e.value.kind == "unsupported"
    s2 = serve({("POST", "/api/v1/models/load"): (500, {"error": "not enough memory"})})
    with pytest.raises(ProviderError) as e:
        LMStudio(s2.url).load("m", {})
    assert e.value.kind == "http" and "not enough memory" in str(e.value)


def test_generic_openai_has_no_model_lifecycle():
    p = OpenAICompat("http://127.0.0.1:1")
    assert not p.supports_load
    assert p.load("m", {}) is None
    p.unload("m")  # no-op
