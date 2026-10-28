"""Fake local model servers. Response shapes follow the Ollama and LM Studio docs; where a
shape is assumed rather than documented (LM Studio's older /api/v0/models, `lms ls --json`,
`ollama list` text) it is marked ASSUMED so a real-world mismatch is easy to spot."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OLLAMA_VERSION = {"version": "0.5.7"}
OLLAMA_TAGS = {"models": [
    {"name": "llama3.2:3b", "model": "llama3.2:3b", "modified_at": "2025-01-01T00:00:00Z", "size": 2019393189,
     "digest": "abc", "details": {"parent_model": "", "format": "gguf", "family": "llama", "families": ["llama"],
                                  "parameter_size": "3.2B", "quantization_level": "Q4_K_M"}},
    {"name": "nomic-embed-text:latest", "model": "nomic-embed-text:latest", "size": 274302450, "digest": "def",
     "details": {"format": "gguf", "family": "nomic-bert", "families": ["nomic-bert"], "parameter_size": "137M",
                 "quantization_level": "F16"}},
    {"name": "qwen2.5-coder:7b", "model": "qwen2.5-coder:7b", "size": 4683087332, "digest": "ghi",
     "details": {"format": "gguf", "family": "qwen2", "parameter_size": "7.6B", "quantization_level": "Q4_K_M"}},
]}
OLLAMA_PS = {"models": [{"name": "llama3.2:3b", "model": "llama3.2:3b", "size": 2500000000, "digest": "abc",
                         "details": {}, "expires_at": "2025-01-01T00:05:00Z", "size_vram": 2500000000}]}
OLLAMA_SHOW = {"modelfile": "", "parameters": "", "template": "", "details": {},
               "model_info": {"general.architecture": "llama", "llama.context_length": 131072}, "capabilities": ["completion"]}

# Documented fields of GET /api/v1/models.
LMS_V1 = {"models": [
    {"type": "llm", "publisher": "qwen", "key": "qwen/qwen2.5-coder-7b", "display_name": "Qwen2.5 Coder 7B",
     "architecture": "qwen2", "quantization": {"name": "Q4_K_M", "bits_per_weight": 4}, "size_bytes": 4683087332,
     "params_string": "7B", "loaded_instances": [{"id": "qwen/qwen2.5-coder-7b", "config": {"context_length": 4096}}],
     "max_context_length": 32768, "format": "gguf", "capabilities": {"vision": False, "trained_for_tool_use": True}},
    {"type": "llm", "publisher": "meta", "key": "llama-3.2-3b-instruct", "display_name": "Llama 3.2 3B Instruct",
     "architecture": "llama", "quantization": {"name": "Q8_0", "bits_per_weight": 8}, "size_bytes": 3400000000,
     "params_string": "3B", "loaded_instances": [], "max_context_length": 131072, "format": "gguf"},
    {"type": "embedding", "publisher": "nomic-ai", "key": "text-embedding-nomic-embed-text-v1.5",
     "display_name": "Nomic Embed", "size_bytes": 84000000, "params_string": None, "loaded_instances": [],
     "max_context_length": 2048, "format": "gguf"},
]}
# ASSUMED shape of the older GET /api/v0/models.
LMS_V0 = {"object": "list", "data": [
    {"id": "qwen2.5-coder-7b", "object": "model", "type": "llm", "publisher": "qwen", "arch": "qwen2",
     "quantization": "Q4_K_M", "state": "loaded", "max_context_length": 32768},
    {"id": "llama-3.2-3b", "object": "model", "type": "vlm", "arch": "llama", "quantization": "Q8_0",
     "state": "not-loaded", "max_context_length": 131072},
    {"id": "text-embedding-x", "object": "model", "type": "embeddings", "state": "not-loaded"},
]}
OPENAI_MODELS = {"object": "list", "data": [{"id": "my-chat-model", "object": "model"},
                                             {"id": "bge-small-embed", "object": "model"}]}
# ASSUMED output of `lms ls --json`.
LMS_LS_JSON = [
    {"type": "llm", "modelKey": "qwen/qwen2.5-coder-7b", "displayName": "Qwen2.5 Coder 7B", "path": "qwen/q/q.gguf",
     "sizeBytes": 4683087332, "paramsString": "7B", "architecture": "qwen2", "maxContextLength": 32768,
     "quantization": {"name": "Q4_K_M"}},
    {"type": "embedding", "modelKey": "nomic-embed", "displayName": "Nomic", "sizeBytes": 84000000},
]


class FakeServer:
    """routes: {(METHOD, path): body | (status, body) | (status, body, headers) | callable(handler, body)}"""

    def __init__(self, routes, port=0):
        self.routes, self.requests = routes, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _serve(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw
                fake.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
                route = fake.routes.get((self.command, self.path.split("?")[0]))
                if route is None:
                    return self._send(404, {"error": "not found"})
                if callable(route):
                    return route(self, body)
                if isinstance(route, tuple):
                    return self._send(*route)
                self._send(200, route)

            def _send(self, status, body, headers=None):
                data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _serve

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def slow(seconds, body=None):
    def route(handler, _body):
        time.sleep(seconds)
        handler._send(200, body if body is not None else {})
    return route


def ollama_routes(over=None):
    r = {("GET", "/api/version"): OLLAMA_VERSION, ("GET", "/api/tags"): OLLAMA_TAGS,
         ("GET", "/api/ps"): OLLAMA_PS, ("POST", "/api/show"): OLLAMA_SHOW}
    r.update(over or {})
    return r


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def write_ollama_models(root):
    """ASSUMED on-disk layout of Ollama's model store: manifests/<registry>/<namespace>/<model>/<tag> (JSON)."""
    def manifest(rel, sizes):
        p = root / "manifests" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schemaVersion": 2, "config": {"size": sizes[0]}, "layers": [{"size": n} for n in sizes[1:]]}))
    manifest("registry.ollama.ai/library/llama3.2/3b", [100, 2000, 19])            # 2119 bytes
    manifest("registry.ollama.ai/library/nomic-embed-text/latest", [10, 500])
    manifest("registry.ollama.ai/someuser/custom/v2", [10, 90])
    manifest("hf.co/bartowski/Llama-3.2-3B-Instruct-GGUF/Q4_K_M", [5, 995])
    return root


# ----------------------------------------------------------------- streaming routes
def _stream(handler, lines, delay=0.0, status=200, ctype="application/x-ndjson", hang_after=None):
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.end_headers()
    try:
        for i, line in enumerate(lines):
            if hang_after is not None and i == hang_after:
                time.sleep(30)
            handler.wfile.write(line if isinstance(line, bytes) else line.encode())
            handler.wfile.flush()
            if delay:
                time.sleep(delay)
    except (BrokenPipeError, ConnectionResetError):
        pass


def ndjson_route(chunks, delay=0.0, **kw):
    """chunks: dicts (one JSON per line) or raw str/bytes lines."""
    lines = [(json.dumps(c) + "\n") if isinstance(c, dict) else c for c in chunks]
    return lambda handler, body: _stream(handler, lines, delay, **kw)


def sse_route(chunks, delay=0.0, **kw):
    lines = []
    for c in chunks:
        lines.append(c if isinstance(c, (str, bytes)) else "data: " + json.dumps(c) + "\n\n")
    return lambda handler, body: _stream(handler, lines, delay, ctype="text/event-stream", **kw)


def ollama_tokens(*texts, done_reason="stop", extra=None):
    out = [{"model": "m", "message": {"role": "assistant", "content": t}, "done": False} for t in texts]
    out.append({"model": "m", "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": done_reason,
                "prompt_eval_count": 12, "eval_count": len(texts), "eval_duration": 500_000_000, **(extra or {})})
    return out


def openai_tokens(*texts, finish="stop"):
    out = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}]
    out += [{"choices": [{"index": 0, "delta": {"content": t}}]} for t in texts]
    out.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    out.append("data: [DONE]\n\n")
    return out
