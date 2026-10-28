import json
import threading
import urllib.error
import urllib.request

import pytest

from code_explainer.server import Session, clean_progress, make_handler, signature
from http.server import ThreadingHTTPServer


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_EXPLAINER_HOME", str(tmp_path / "home"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def a():\n    return 1\n")
    session = Session(proj, "proj", None, None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    yield proj, session, base
    httpd.shutdown()
    httpd.server_close()


def call(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_serves_page_data_and_version(served):
    proj, session, base = served
    code, body = call(base + "/")
    assert code == 200 and b"Code Explainer" in body and b'"version":1' in body
    code, body = call(base + "/api/data")
    assert json.loads(body)["data"]["repo"]["name"] == "proj"
    assert json.loads(call(base + "/api/version")[1])["version"] == 1
    assert call(base + "/nope")[0] == 404
    assert call(base + "/../../etc/passwd")[0] == 404


def test_reanalyses_when_files_change(served):
    proj, session, base = served
    before = session.version
    assert not session.changed()
    (proj / "b.py").write_text("def b():\n    return 2\n")
    assert session.changed()
    session.refresh()
    assert session.version == before + 1
    assert any(f["path"] == "b.py" for f in session.data["files"])
    assert not session.changed()


def test_broken_analysis_keeps_last_good_data(served, monkeypatch):
    proj, session, base = served
    import code_explainer.server as srv
    monkeypatch.setattr(srv, "analyze_repo", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    v = session.version
    session.refresh()
    assert session.version == v and "boom" in session.error and session.data["repo"]["name"] == "proj"


def test_progress_roundtrip_and_validation(served):
    proj, session, base = served
    js = {"Content-Type": "application/json"}
    payload = {"understood": {"a.py": True, "b.py": False}, "notes": {"a.py": "hi", "x": 5}, "bookmarks": ["a.py", 3],
               "evil": "<script>"}
    code, body = call(base + "/api/progress", json.dumps(payload).encode(), js)
    saved = json.loads(body)
    assert code == 200 and saved == {"understood": {"a.py": True}, "notes": {"a.py": "hi"}, "bookmarks": ["a.py"]}
    assert json.loads(call(base + "/api/progress")[1]) == saved
    assert session.progress_path.is_file() and str(proj) not in str(session.progress_path)  # never written into the repo
    assert call(base + "/api/progress", b"{}", {"Content-Type": "text/plain"})[0] == 415
    assert call(base + "/api/progress", b"not json", js)[0] == 400
    assert call(base + "/api/progress", b"{}", {**js, "Origin": "http://evil.example"})[0] == 403


def test_rejects_foreign_host_header(served):
    proj, session, base = served
    code, _ = call(base + "/api/data", headers={"Host": "evil.example"})
    assert code == 403


def test_clean_progress_handles_garbage():
    assert clean_progress("x") == {"understood": {}, "notes": {}, "bookmarks": []}
    assert clean_progress({"notes": {"a": "   "}})["notes"] == {}


def test_signature_ignores_vendored_dirs(tmp_path):
    (tmp_path / "a.py").write_text("x")
    s1 = signature(tmp_path)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "z.js").write_text("y")
    assert signature(tmp_path) == s1
