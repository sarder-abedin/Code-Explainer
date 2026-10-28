"""Fake `ollama` and `lms` programs for lifecycle tests.

Run as a script it becomes the fake *server*:  fake_backends.py ollama|lms PORT LOGFILE
`install(dir)` writes executable `ollama` and `lms` shell wrappers that behave like the real
CLIs closely enough to test starting, loading, unloading and stopping real processes.
"""
import json
import os
import stat
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ai_fakes import FakeServer, ndjson_route, ollama_tokens, openai_tokens, sse_route, LMS_V1, OLLAMA_TAGS  # noqa: E402

MODELS = ["llama3.2:3b", "qwen2.5-coder:7b"]


def serve(kind, port, log):
    loaded = set(filter(None, os.environ.get("FAKE_PRELOADED", "").split(",")))
    lock = threading.Lock()

    def record(**kw):
        with lock, open(log, "a") as f:
            f.write(json.dumps({"server": kind, **kw}) + "\n")

    if kind == "ollama":
        def chat(handler, body):
            if body.get("messages") == []:
                if body.get("keep_alive") == 0:
                    loaded.discard(body["model"])
                    record(op="unload", model=body["model"])
                else:
                    loaded.add(body["model"])
                    record(op="load", model=body["model"], body=body)
                return handler._send(200, {"model": body["model"], "done": True})
            return ndjson_route(ollama_tokens("Hi ", "there"))(handler, body)

        def ps(handler, body):
            handler._send(200, {"models": [{"name": m, "model": m, "size": 1, "digest": "d"} for m in sorted(loaded)]})
        routes = {("GET", "/api/version"): {"version": "0.0-fake"}, ("GET", "/api/tags"): OLLAMA_TAGS,
                  ("GET", "/api/ps"): ps, ("POST", "/api/chat"): chat}
    else:
        def models(handler, body):
            ms = json.loads(json.dumps(LMS_V1))
            for m in ms["models"]:
                m["loaded_instances"] = [{"id": m["key"], "config": {}}] if m["key"] in loaded else []
            handler._send(200, ms)

        def load(handler, body):
            if os.environ.get("FAKE_LOAD_FAILS"):
                return handler._send(500, {"error": "not enough memory"})
            loaded.add(body["model"])
            record(op="load", model=body["model"], body=body)
            handler._send(200, {"type": "llm", "instance_id": body["model"], "load_time_seconds": 0.1, "status": "loaded"})

        def unload(handler, body):
            loaded.discard(body["instance_id"])
            record(op="unload", model=body["instance_id"])
            handler._send(200, {"instance_id": body["instance_id"]})
        routes = {("GET", "/api/v1/models"): models, ("POST", "/api/v1/models/unload"): unload,
                  ("POST", "/v1/chat/completions"): sse_route(openai_tokens("Hi ", "there"))}
        if not os.environ.get("FAKE_NO_LOAD_ENDPOINT"):
            routes[("POST", "/api/v1/models/load")] = load
    FakeServer(routes, port=port)
    record(op="listening", port=port)
    threading.Event().wait()


def install(directory, log, python=sys.executable, ollama=True, lms=True, ollama_script=None):
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    me = str(Path(__file__).resolve())

    def write(name, text):
        p = d / name
        p.write_text("#!/bin/sh\n" + text)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    if ollama:
        write("ollama", ollama_script or (
            f'echo "$@" >> "{log}.argv"\n'
            f'if [ "$1" = serve ]; then\n  PORT="${{OLLAMA_HOST##*:}}"\n  exec "{python}" "{me}" ollama "$PORT" "{log}"\nfi\nexit 0\n'))
    if lms:
        write("lms", f'''echo "$@" >> "{log}.argv"
case "$1 $2" in
  "server start")
    shift 2; PORT=1234
    while [ $# -gt 0 ]; do if [ "$1" = --port ]; then PORT="$2"; shift; fi; shift; done
    "{python}" "{me}" lms "$PORT" "{log}" >/dev/null 2>&1 &
    echo $! > "{log}.lmspid"; echo "Success! Server is now running on port $PORT"; exit 0;;
  "server stop")
    [ -f "{log}.lmspid" ] && kill $(cat "{log}.lmspid") 2>/dev/null; echo stopped; exit 0;;
esac
if [ "$1" = load ]; then echo "$@" >> "{log}.cliload"; exit 0; fi
if [ "$1" = unload ]; then echo "$@" >> "{log}.cliunload"; exit 0; fi
exit 0
''')
    return d


if __name__ == "__main__":
    serve(sys.argv[1], int(sys.argv[2]), sys.argv[3])
