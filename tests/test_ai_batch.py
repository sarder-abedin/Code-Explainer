"""`analyze --ai`: choosing what to explain, running the batch, and embedding the result in the report."""
import json
import threading

import pytest

from ai_fakes import FakeServer, ndjson_route, ollama_routes, ollama_tokens
from code_explainer.ai import batch, settings
from code_explainer.ai.batch import BatchError, BatchResult
from code_explainer.core import analyze_repo
from code_explainer.report import render_html
from conftest import git, write


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_EXPLAINER_HOME", str(tmp_path / "home"))


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


def plain_data(repo):
    return analyze_repo(repo, "demo")


def change_data(repo):
    return analyze_repo(repo, "demo", base="HEAD~1")


# ----------------------------------------------------------------- choosing what to explain
def test_files_come_in_reading_order_and_tests_are_left_out(repo):
    data = plain_data(repo)
    targets = batch.pick_targets(data, 10)
    assert targets and all(t["kind"] == "file" for t in targets)
    paths = [t["path"] for t in targets]
    assert paths[0] == data["readingOrder"][0]["path"] and len(paths) == len(set(paths))
    assert "tests/test_lib.py" not in paths and all(t["why"] for t in targets)


def test_the_limit_is_respected_and_zero_means_nothing(repo):
    data = plain_data(repo)
    assert len(batch.pick_targets(data, 1)) == 1
    assert batch.pick_targets(data, 0) == []


def test_changes_are_explained_first_but_never_take_more_than_half_the_budget(repo):
    data = change_data(repo)
    changed = [p for p in data["changes"]["files"]]
    assert len(changed) >= 3
    targets = batch.pick_targets(data, 4)
    kinds = [t["kind"] for t in targets]
    assert len(targets) == 4 and kinds[:2] == ["change", "change"]
    assert kinds.count("change") <= 2 and kinds.count("file") >= 1
    assert {t["path"] for t in targets if t["kind"] == "change"} <= set(changed)


def test_unused_room_goes_to_the_remaining_changes(repo):
    data = change_data(repo)
    data["readingOrder"] = []
    targets = batch.pick_targets(data, 10)
    assert [t["kind"] for t in targets] == ["change"] * len(data["changes"]["files"])


def test_a_target_is_never_listed_twice(repo):
    data = change_data(repo)
    keys = [batch.target_key(t["kind"], t["path"]) for t in batch.pick_targets(data, 50)]
    assert len(keys) == len(set(keys))


# ----------------------------------------------------------------- running a batch (against a stand-in service)
class Stub:
    """Plays the part of ChatService.explain with scripted outcomes per path."""

    def __init__(self, outcomes=None):
        self.outcomes, self.calls = outcomes or {}, []

    def explain(self, req, cancel=None):
        self.calls.append(req)
        out = self.outcomes.get(req["path"], "ok")
        if out == "ok" or out == "cached":
            yield {"type": "start", "cached": out == "cached", "title": "T " + req["path"]}
            yield {"type": "token", "text": "About "}
            yield {"type": "token", "text": req["path"]}
            yield {"type": "done", "truncated": False, "cached": out == "cached", "elapsed": 1}
        elif out == "truncated":
            yield {"type": "start", "cached": False, "title": "T"}
            yield {"type": "token", "text": "half"}
            yield {"type": "done", "truncated": True, "cached": False}
        else:
            yield {"type": "error", "kind": out, "message": f"{out} happened", "hint": ""}


TARGETS = [{"kind": "file", "path": p, "why": "w"} for p in ("a.py", "b.py", "c.py")]


def test_every_target_is_explained_and_counted():
    r = batch.run_batch(Stub({"b.py": "cached"}), TARGETS, {"level": "plain"})
    assert [i["text"] for i in r.items.values()] == ["About a.py", "About b.py", "About c.py"]
    assert (r.generated, r.reused, r.failed, r.stopped, r.aborted) == (2, 1, [], False, "")
    item = r.items["file|b.py"]
    assert item["kind"] == "file" and item["path"] == "b.py" and item["title"] == "T b.py"


def test_the_requested_parameters_are_sent_with_each_request():
    stub = Stub()
    batch.run_batch(stub, TARGETS[:1], {"level": "tech"})
    assert stub.calls == [{"kind": "file", "path": "a.py", "params": {"level": "tech"}}]


def test_one_failure_does_not_stop_the_rest():
    r = batch.run_batch(Stub({"a.py": "bad_request", "b.py": "truncated"}), TARGETS, {})
    assert list(r.items) == ["file|c.py"]
    assert [f["path"] for f in r.failed] == ["a.py", "b.py"]
    assert "bad_request happened" in r.failed[0]["message"]
    assert "cut off" in r.failed[1]["message"] and "Max answer length" in r.failed[1]["message"]


@pytest.mark.parametrize("kind", ["not_ready", "unreachable"])
def test_a_dead_model_ends_the_run_instead_of_failing_every_file(kind):
    stub = Stub({"b.py": kind})
    r = batch.run_batch(stub, TARGETS, {})
    assert list(r.items) == ["file|a.py"] and r.aborted == f"{kind} happened"
    assert [c["path"] for c in stub.calls] == ["a.py", "b.py"]      # c.py was never tried


def test_progress_reports_each_step():
    seen = []
    batch.run_batch(Stub({"b.py": "cached", "c.py": "bad_request"}), TARGETS, {},
                    progress=lambda i, n, t, state, note: seen.append((i, n, t["path"], state)))
    assert seen == [(1, 3, "a.py", "start"), (1, 3, "a.py", "generated"),
                    (2, 3, "b.py", "start"), (2, 3, "b.py", "reused"),
                    (3, 3, "c.py", "start"), (3, 3, "c.py", "failed")]


def test_stopping_keeps_what_is_finished_and_skips_the_rest():
    cancel = threading.Event()
    stub = Stub()

    def progress(i, n, t, state, note):
        if state == "generated" and i == 2:
            cancel.set()
    r = batch.run_batch(stub, TARGETS, {}, cancel=cancel, progress=progress)
    assert list(r.items) == ["file|a.py", "file|b.py"] and r.stopped and len(stub.calls) == 2


def test_ctrl_c_is_a_clean_stop_not_a_crash():
    def progress(i, n, t, state, note):
        if state == "generated" and i == 1:
            raise KeyboardInterrupt
    r = batch.run_batch(Stub(), TARGETS, {}, progress=progress)
    assert list(r.items) == ["file|a.py"] and r.stopped


def test_an_answer_cut_short_by_a_stop_is_not_kept():
    class Halts(Stub):
        def explain(self, req, cancel=None):
            yield {"type": "start", "cached": False, "title": "T"}
            yield {"type": "token", "text": "partial"}
            yield {"type": "cancelled"}
    r = batch.run_batch(Halts(), TARGETS, {})
    assert r.items == {} and r.stopped and r.failed == []


def test_a_model_that_says_nothing_is_a_failure():
    class Silent(Stub):
        def explain(self, req, cancel=None):
            yield {"type": "start", "cached": False, "title": "T"}
            yield {"type": "token", "text": "   "}
            yield {"type": "done", "truncated": False, "cached": False}
    r = batch.run_batch(Silent(), TARGETS[:1], {})
    assert r.items == {} and "no answer" in r.failed[0]["message"]


# ----------------------------------------------------------------- embedding the result
def test_attach_stores_a_small_labelled_block(repo):
    data = plain_data(repo)
    r = BatchResult(items={"file|app/lib.py": {"kind": "file", "path": "app/lib.py", "title": "T", "text": "Hi"}})
    batch.attach(data, r, provider="ollama", model="m1", level="plain")
    block = data["aiExplain"]
    assert block["model"] == "m1" and block["provider"] == "ollama" and block["level"] == "plain"
    assert block["items"]["file|app/lib.py"]["text"] == "Hi" and block["generated"]


def test_nothing_is_attached_when_nothing_was_written(repo):
    data = plain_data(repo)
    batch.attach(data, BatchResult(), provider="ollama", model="m1", level="plain")
    assert "aiExplain" not in data


def test_text_that_tries_to_close_the_script_cannot_break_the_page(repo):
    data = plain_data(repo)
    evil = "</script><script>window.pwned=1</script> <!-- &"
    batch.attach(data, BatchResult(items={"file|app/lib.py": {"kind": "file", "path": "app/lib.py", "title": "T", "text": evil}}),
                 provider="ollama", model="m", level="kid")
    html = render_html(data)
    assert "<script>window.pwned" not in html and evil not in html
    start = html.index('id="data">') + len('id="data">')
    assert json.loads(html[start:html.index("</script>", start)])["aiExplain"]["items"]["file|app/lib.py"]["text"] == evil


# ----------------------------------------------------------------- choosing the model
def fake_ollama(servers, reply=("Short ", "answer.")):
    def chat(handler, body):
        if body.get("messages") == []:
            return handler._send(200, {"model": body["model"], "done": True})
        return ndjson_route(ollama_tokens(*reply))(handler, body)
    s = servers(ollama_routes({("POST", "/api/chat"): chat}))
    settings.update({"endpoints": [{"provider": "ollama", "url": s.url}], "autostart": False})
    return s


def test_the_model_chosen_in_the_ai_tab_is_the_default(servers):
    s = fake_ollama(servers)
    settings.update({"selected": {"provider": "ollama", "endpoint": s.url, "model": "llama3.2:3b"}})
    assert batch.choose_model(settings.load(), None) == ("ollama", s.url, "llama3.2:3b")


def test_with_no_choice_you_are_told_how_to_make_one(servers):
    fake_ollama(servers)
    with pytest.raises(BatchError) as e:
        batch.choose_model(settings.load(), None)
    assert "--ai-model" in str(e.value) and "AI tab" in str(e.value)


def test_a_model_can_be_named_on_the_command_line(servers):
    s = fake_ollama(servers)
    assert batch.choose_model(settings.load(), "qwen2.5-coder:7b") == ("ollama", s.url, "qwen2.5-coder:7b")
    assert batch.choose_model(settings.load(), "ollama:llama3.2:3b") == ("ollama", s.url, "llama3.2:3b")


def test_an_unknown_model_lists_what_is_available(servers):
    fake_ollama(servers)
    with pytest.raises(BatchError) as e:
        batch.choose_model(settings.load(), "nope")
    msg = str(e.value)
    assert "nope" in msg and "llama3.2:3b" in msg and "qwen2.5-coder:7b" in msg
    assert "nomic-embed-text" not in msg                                  # an embedding model can not explain code


def test_an_embedding_model_is_refused(servers):
    fake_ollama(servers)
    with pytest.raises(BatchError):
        batch.choose_model(settings.load(), "nomic-embed-text:latest")


def test_the_provider_in_the_name_must_match(servers):
    fake_ollama(servers)
    with pytest.raises(BatchError):
        batch.choose_model(settings.load(), "lmstudio:llama3.2:3b")


# ----------------------------------------------------------------- the whole run, against a fake Ollama
def chats(s):
    return [r for r in s.requests if r["path"] == "/api/chat" and r["body"]["messages"]]


def unloads(s):
    return [r for r in s.requests if r["path"] == "/api/chat" and r["body"].get("messages") == [] and r["body"].get("keep_alive") == 0]


def test_a_full_run_explains_the_files_and_unloads_the_model(servers, repo, tmp_path):
    s = fake_ollama(servers)
    data = plain_data(repo)
    seen = []
    r = batch.generate(data, tmp_path / "cache", "qwen2.5-coder:7b", limit=2, level="plain",
                       progress=lambda *a: seen.append(a[3]))
    assert len(r.items) == 2 and r.generated == 2 and r.reused == 0 and not r.failed
    assert all(i["text"] == "Short answer." for i in r.items.values())
    assert len(chats(s)) == 2 and unloads(s)                               # the tool unloads what it loaded
    assert "start" in seen and "generated" in seen
    assert data["aiExplain"]["model"] == "qwen2.5-coder:7b" and data["aiExplain"]["level"] == "plain"
    assert data["aiExplain"]["items"].keys() == r.items.keys()


def test_running_again_reuses_every_saved_answer(servers, repo, tmp_path):
    s = fake_ollama(servers)
    for expected in ((2, 0), (0, 2)):
        r = batch.generate(plain_data(repo), tmp_path / "cache", "llama3.2:3b", limit=2, level="plain")
        assert (r.generated, r.reused) == expected
    assert len(chats(s)) == 2                                               # the second run never asked the model


def test_resume_picks_up_after_a_stop(servers, repo, tmp_path):
    s = fake_ollama(servers)
    cancel = threading.Event()

    def progress(i, n, t, state, note):
        if state == "generated" and i == 1:
            cancel.set()
    r1 = batch.generate(plain_data(repo), tmp_path / "cache", "llama3.2:3b", limit=3, level="plain", progress=progress, cancel=cancel)
    assert r1.stopped and r1.generated == 1
    r2 = batch.generate(plain_data(repo), tmp_path / "cache", "llama3.2:3b", limit=3, level="plain")
    assert not r2.stopped and r2.reused == 1 and r2.generated == len(r2.items) - 1
    assert len(chats(s)) == len(r2.items)                                   # each file was explained exactly once overall


def test_changes_are_explained_with_their_patch(servers, repo, tmp_path):
    s = fake_ollama(servers)
    r = batch.generate(change_data(repo), tmp_path / "cache", "llama3.2:3b", limit=2, level="kid")
    assert any(k.startswith("change|") for k in r.items)
    prompts = json.dumps([c["body"]["messages"] for c in chats(s)])
    assert "patch for" in prompts


def test_a_model_that_is_not_downloaded_stops_before_any_work(servers, repo, tmp_path):
    s = fake_ollama(servers)
    settings.update({"selected": {"provider": "ollama", "endpoint": s.url, "model": "ghost:1b"}})
    with pytest.raises(BatchError) as e:
        batch.generate(plain_data(repo), tmp_path / "cache", None, limit=2, level="plain")
    assert "ghost:1b" in str(e.value) and chats(s) == []


def test_a_server_that_is_off_is_reported_plainly(servers, repo, tmp_path):
    s = fake_ollama(servers)
    settings.update({"selected": {"provider": "ollama", "endpoint": s.url, "model": "llama3.2:3b"}})
    s.stop()
    with pytest.raises(BatchError) as e:
        batch.generate(plain_data(repo), tmp_path / "cache", None, limit=1, level="plain")
    assert str(e.value)


def test_the_level_defaults_to_the_one_saved_in_the_ai_tab(servers, repo, tmp_path):
    s = fake_ollama(servers)
    settings.update({"params": {"level": "tech"}})
    r = batch.generate(plain_data(repo), tmp_path / "cache", "llama3.2:3b", limit=1, level=None)
    assert r.level == "tech" and "technical" in json.dumps(chats(s)[0]["body"]["messages"][0]).lower()


# ----------------------------------------------------------------- the command line
def run_cli(*argv):
    from code_explainer.cli import main
    return main(list(argv))


def test_analyze_ai_writes_a_report_with_the_explanations(servers, repo, tmp_path, capsys):
    s = fake_ollama(servers)
    out = tmp_path / "r.html"
    code = run_cli("analyze", str(repo), "-o", str(out), "--ai", "--ai-model", "llama3.2:3b", "--ai-limit", "2", "--ai-level", "plain")
    err = capsys.readouterr().err
    assert code == 0 and out.exists()
    html = out.read_text()
    start = html.index('id="data">') + len('id="data">')
    block = json.loads(html[start:html.index("</script>", start)])["aiExplain"]
    assert len(block["items"]) == 2 and block["level"] == "plain" and block["model"] == "llama3.2:3b"
    assert "[1/2]" in err and "[2/2]" in err and "2 explained" in err
    assert len(chats(s)) == 2


def test_a_second_run_says_it_reused_the_saved_answers(servers, repo, tmp_path, capsys):
    fake_ollama(servers)
    args = ("analyze", str(repo), "-o", str(tmp_path / "r.html"), "--ai", "--ai-model", "llama3.2:3b", "--ai-limit", "1")
    assert run_cli(*args) == 0
    capsys.readouterr()
    assert run_cli(*args) == 0
    err = capsys.readouterr().err
    assert "saved" in err and "0 explained" in err


def test_without_a_model_the_report_is_still_written_and_the_exit_code_says_it_went_wrong(servers, repo, tmp_path, capsys):
    fake_ollama(servers)
    out = tmp_path / "r.html"
    assert run_cli("analyze", str(repo), "-o", str(out), "--ai") == 1
    err = capsys.readouterr().err
    assert "AI tab" in err and "without AI" in err
    assert out.exists() and '"aiExplain"' not in out.read_text()


def test_ai_options_need_the_ai_switch(repo, capsys):
    for extra in (["--ai-limit", "3"], ["--ai-model", "x"], ["--ai-level", "kid"]):
        with pytest.raises(SystemExit) as e:
            run_cli("analyze", str(repo), *extra)
        assert e.value.code == 2
    assert "--ai" in capsys.readouterr().err


def test_a_silly_limit_is_rejected(repo):
    for bad in ("0", "-1", "x"):
        with pytest.raises(SystemExit):
            run_cli("analyze", str(repo), "--ai", "--ai-limit", bad)


def test_the_change_review_and_ai_work_together(servers, repo, tmp_path, capsys):
    fake_ollama(servers)
    out = tmp_path / "r.html"
    assert run_cli("analyze", str(repo), "-o", str(out), "--last", "1", "--ai", "--ai-model", "llama3.2:3b", "--ai-limit", "2") == 0
    html = out.read_text()
    start = html.index('id="data">') + len('id="data">')
    data = json.loads(html[start:html.index("</script>", start)])
    assert any(k.startswith("change|") for k in data["aiExplain"]["items"]) and "changes" in data


def test_ctrl_c_still_writes_the_report_with_what_is_done(servers, repo, tmp_path, capsys, monkeypatch):
    fake_ollama(servers)
    real = batch.run_batch

    def interrupt(*a, **kw):
        inner = kw.get("progress")

        def progress(i, n, t, state, note):
            inner and inner(i, n, t, state, note)
            if state == "generated":
                raise KeyboardInterrupt
        return real(*a, **{**kw, "progress": progress})
    monkeypatch.setattr(batch, "run_batch", interrupt)
    out = tmp_path / "r.html"
    code = run_cli("analyze", str(repo), "-o", str(out), "--ai", "--ai-model", "llama3.2:3b", "--ai-limit", "3")
    err = capsys.readouterr().err
    assert code == 0 and out.exists() and '"aiExplain"' in out.read_text()
    assert "stopped" in err.lower() and "run the same command again" in err.lower()


# ----------------------------------------------------------------- parts of the map
def pkg_data(tmp_path):
    write(tmp_path, {"app/__init__.py": "", "app/web/__init__.py": "", "app/web/v.py": "from app.core import p\n\n\ndef home():\n    return p.total([1])\n",
                     "app/core/__init__.py": "", "app/core/p.py": "def total(x):\n    return sum(x)\n", "app/core/q.py": "def q():\n    return 1\n",
                     "app/db/__init__.py": "", "app/db/s.py": "def load():\n    return 1\n"})
    return analyze_repo(tmp_path, "app")


def test_the_biggest_parts_of_the_map_are_picked_but_only_when_the_limit_leaves_room(tmp_path):
    data = pkg_data(tmp_path)
    assert batch.pick_part_targets(data, 4) == []                                          # too small a budget
    one = batch.pick_part_targets(data, 5)
    assert [t["part"] for t in one] == ["app/core"] and one[0]["kind"] == "part" and one[0]["path"] == "app/core" and one[0]["why"]
    two = batch.pick_part_targets(data, 50)
    assert len(two) == batch.MAX_PARTS == 2 and two[0]["part"] == "app/core"
    assert batch.pick_part_targets({"files": []}, 50) == []                                # no map at all


def test_a_full_run_also_explains_parts_and_keeps_to_the_limit(servers, tmp_path):
    s = fake_ollama(servers)
    data = pkg_data(tmp_path / "p")
    r = batch.generate(data, tmp_path / "cache", "qwen2.5-coder:7b", limit=5, level="plain")
    assert len(r.items) <= 5 and "part|app/core" in r.items and r.items["part|app/core"]["kind"] == "part"
    assert data["aiExplain"]["items"]["part|app/core"]["text"] == "Short answer."
    assert len(chats(s)) == len(r.items) == r.generated


def test_files_and_parts_together_never_exceed_the_limit(tmp_path):
    files = {"app/__init__.py": "", "app/web/__init__.py": "", "app/core/__init__.py": "", "app/db/__init__.py": ""}
    for i in range(6):
        files[f"app/web/v{i}.py"] = f"from app.core import p{i}\n\n\ndef h{i}():\n    return p{i}.f()\n"
        files[f"app/core/p{i}.py"] = "def f():\n    return 1\n"
    files["app/web/main.py"] = "".join(f"from app.web import v{i}\n" for i in range(6)) + "\n\ndef run():\n    return 1\n"
    files["app/db/s.py"] = "\n".join(f"x{i} = {i}" for i in range(80)) + "\n"
    write(tmp_path, files)
    data = analyze_repo(tmp_path, "app")
    assert len(batch.pick_targets(data, 10)) == 10
    picked = batch.pick_all(data, 10)
    assert len(picked) == 10 and [t["kind"] for t in picked].count("part") == 2
    assert [t["kind"] for t in picked][-2:] == ["part", "part"]                              # the parts come after the files
    assert batch.pick_all(data, 4) == batch.pick_targets(data, 4)


def test_parts_are_chosen_by_how_many_files_they_hold_before_how_big_they_are(tmp_path):
    files = {"app/__init__.py": "", "app/web/__init__.py": "", "app/web/v.py": "\n".join(f"x{i} = {i}" for i in range(200)) + "\n",
             "app/core/__init__.py": "", "app/core/a.py": "def a():\n    return 1\n", "app/core/b.py": "def b():\n    return 2\n",
             "app/db/__init__.py": "", "app/db/s.py": "def load():\n    return 1\n"}
    write(tmp_path, files)
    got = [t["part"] for t in batch.pick_part_targets(analyze_repo(tmp_path, "app"), 50)]
    assert got == ["app/core", "app/web"]
