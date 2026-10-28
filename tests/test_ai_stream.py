import threading
import time

import pytest

from ai_fakes import FakeServer, free_port, ndjson_route
from code_explainer.ai.http import ProviderError
from code_explainer.ai.stream import Cancelled, LineStream


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


def lines_of(url, cancel=None, idle=5.0, **kw):
    cancel = cancel or threading.Event()
    return list(LineStream(url, {"x": 1}, {}, cancel, idle_timeout=idle, **kw).lines())


def test_yields_each_line_and_ends(serve):
    s = serve({("POST", "/s"): ndjson_route([{"a": 1}, {"a": 2}, "plain\n"])})
    got = [l for l in lines_of(s.url + "/s") if l is not None]
    assert got == [b'{"a": 1}\n', b'{"a": 2}\n', b"plain\n"]
    assert s.requests[0]["body"] == {"x": 1} and s.requests[0]["headers"]["Content-Type"] == "application/json"


def test_http_errors_carry_status_and_server_message(serve):
    s = serve({("POST", "/s"): (404, {"error": "model 'x' not found"}), ("POST", "/t"): (500, b"boom")})
    with pytest.raises(ProviderError) as e:
        lines_of(s.url + "/s")
    assert e.value.kind == "http" and e.value.status == 404 and "not found" in str(e.value)
    with pytest.raises(ProviderError) as e:
        lines_of(s.url + "/t")
    assert e.value.status == 500


def test_unreachable_server():
    with pytest.raises(ProviderError) as e:
        lines_of(f"http://127.0.0.1:{free_port()}/s")
    assert e.value.kind == "unreachable"


def test_cancel_stops_quickly_even_while_the_server_is_silent(serve):
    s = serve({("POST", "/s"): ndjson_route(["one\n", "two\n", "three\n"], hang_after=1)})
    cancel = threading.Event()
    stream = LineStream(s.url + "/s", {}, {}, cancel, idle_timeout=30).lines()
    got = []
    t0 = time.time()
    threading.Timer(0.5, cancel.set).start()
    with pytest.raises(Cancelled):
        for line in stream:
            if line is not None:
                got.append(line)
    assert got == [b"one\n"] and time.time() - t0 < 3                 # did not wait for the 30 s hang


def test_cancel_while_waiting_for_the_first_byte(serve):
    from ai_fakes import slow
    s = serve({("POST", "/s"): slow(20)})
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()
    t0 = time.time()
    with pytest.raises(Cancelled):
        lines_of(s.url + "/s", cancel=cancel, idle=30)
    assert time.time() - t0 < 3


def test_cancel_before_start_never_connects(serve):
    s = serve({("POST", "/s"): ndjson_route(["x\n"])})
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        lines_of(s.url + "/s", cancel=cancel)


def test_idle_timeout_when_the_model_goes_silent(serve):
    s = serve({("POST", "/s"): ndjson_route(["one\n", "two\n"], hang_after=1)})
    t0 = time.time()
    with pytest.raises(ProviderError) as e:
        lines_of(s.url + "/s", idle=0.6)
    assert e.value.kind == "timeout" and time.time() - t0 < 4


def test_emits_ticks_while_idle_so_a_closed_browser_is_noticed(serve):
    s = serve({("POST", "/s"): ndjson_route(["one\n", "two\n"], hang_after=1)})
    stream = LineStream(s.url + "/s", {}, {}, threading.Event(), idle_timeout=3, tick_seconds=0.2).lines()
    seen = []
    for line in stream:
        seen.append(line)
        if seen.count(None) >= 2:
            break
    assert seen[0] == b"one\n" and seen.count(None) >= 2


def test_closing_the_generator_closes_the_connection(serve):
    s = serve({("POST", "/s"): ndjson_route([f"{i}\n" for i in range(100)], delay=0.05)})
    g = LineStream(s.url + "/s", {}, {}, threading.Event(), idle_timeout=5).lines()
    next(g)
    g.close()                                                         # must not hang or leak a thread
    time.sleep(0.3)
    assert not [t for t in threading.enumerate() if t.name == "ai-stream-reader" and t.is_alive()]


def test_loopback_only_is_enforced_here_too():
    with pytest.raises(ProviderError) as e:
        lines_of("http://203.0.113.9:1234/s")
    assert e.value.kind == "blocked"


def test_does_not_follow_redirects(serve):
    s = serve({("POST", "/s"): (302, b"", {"Location": "http://127.0.0.1:1/x"})})
    with pytest.raises(ProviderError) as e:
        lines_of(s.url + "/s")
    assert e.value.status == 302
