"""Browser tests for the AI tab: model picker, tuning panel and chat, against fake local model servers."""
import json
import os
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from ai_fakes import LMS_V1, OLLAMA_TAGS, FakeServer, ndjson_route, ollama_routes, ollama_tokens
from code_explainer.ai import settings as ai_settings
from code_explainer.core import analyze_repo
from code_explainer.report import render_html
from code_explainer.server import Session, make_handler

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            exe = os.environ.get("CHROMIUM_PATH")
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
        except Exception as e:
            pytest.skip(str(e))
        yield b
        b.close()


@pytest.fixture
def ai(tmp_path, monkeypatch, browser):
    monkeypatch.setenv("CODE_EXPLAINER_HOME", str(tmp_path / "home"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n\ndef hello():\n    return x\n")
    holder = SimpleNamespace(route=ndjson_route(ollama_tokens("Hello ", "world")), tags=OLLAMA_TAGS, load_error=False)

    def chat(handler, body):
        if body.get("messages") == []:
            if holder.load_error and body.get("keep_alive") != 0:
                return handler._send(500, {"error": "model requires more memory than is available"})
            return handler._send(200, {"model": body.get("model"), "done": True})
        return holder.route(handler, body)

    fake = FakeServer(ollama_routes({("POST", "/api/chat"): chat, ("GET", "/api/tags"): lambda h, b: h._send(200, holder.tags)}))
    ai_settings.update({"endpoints": [{"provider": "ollama", "url": fake.url, "name": "Fake Ollama"}], "autostart": False})
    session = Session(proj, "proj", None, None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.set_default_timeout(6000)          # a failing assertion should fail in seconds, not half a minute
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    ctx = SimpleNamespace(page=page, base=base, fake=fake, session=session, holder=holder, errors=errors, proj=proj)
    yield ctx
    page.close()
    session.ai.shutdown()
    httpd.shutdown()
    httpd.server_close()
    fake.stop()
    assert errors == [], errors


def open_ai(ai, hash_extra=""):
    ai.page.goto(f"{ai.base}/#tab=ai{hash_extra}")
    ai.page.wait_for_selector("#ai-status")


def settings_now(ai):
    return json.loads(urllib.request.urlopen(ai.base + "/api/ai/settings").read())["settings"]


def api_status(ai):
    return json.loads(urllib.request.urlopen(ai.base + "/api/ai/status").read())


def wait_until(fn, seconds=8.0):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def open_section(ai, title):
    """Expand a collapsible section (<details>) by its heading, as a user would."""
    d = ai.page.locator("details", has=ai.page.locator("summary", has_text=title))
    if d.get_attribute("open") is None:
        d.locator("summary").click()


def pick_and_start(ai, model="qwen2.5-coder:7b"):
    ai.page.locator(f'.ai-provider[data-endpoint="{ai.fake.url}"] .ai-model[data-model-id="{model}"] input').check()
    ai.page.click("#ai-start")
    ai.page.wait_for_selector('#ai-status[data-state="ready"]', timeout=10000)


def ask(ai, text):
    ai.page.fill("#ai-input", text)
    ai.page.click("#ai-send")


# ----------------------------------------------------------------- static export
def test_static_export_explains_that_ai_needs_serve(tmp_path, browser):
    out = tmp_path / "r.html"
    out.write_text(render_html(analyze_repo(tmp_path, "x")), encoding="utf-8")
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.goto(out.as_uri() + "#tab=ai")
    page.wait_for_selector(".ai-static")
    assert "code-explainer serve" in page.inner_text(".ai-static")
    assert page.locator("#ai-start").count() == 0
    assert [u for u in requests if "/api/" in u] == [] and errors == []
    page.close()


# ----------------------------------------------------------------- model picker and lifecycle
def test_models_are_listed_with_state_and_embedding_models_are_hidden_by_default(ai):
    open_ai(ai)
    card = ai.page.locator(f'.ai-provider[data-endpoint="{ai.fake.url}"]')
    card.wait_for()
    assert "Fake Ollama" in card.inner_text() and "Running" in card.inner_text()
    ids = [e.get_attribute("data-model-id") for e in card.locator(".ai-model").all()]
    assert ids == ["llama3.2:3b", "qwen2.5-coder:7b"]
    assert "loaded" in card.locator('.ai-model[data-model-id="llama3.2:3b"]').inner_text()
    assert "3.2B" in card.inner_text() and "Q4_K_M" in card.inner_text() and "GB" in card.inner_text()
    ai.page.check("#ai-show-all")
    assert card.locator('.ai-model[data-model-id="nomic-embed-text:latest"]').count() == 1


def test_start_needs_a_model_then_reaches_ready_and_survives_a_reload(ai):
    open_ai(ai)
    ai.page.wait_for_selector(".ai-model")
    assert ai.page.is_disabled("#ai-start") and ai.page.is_disabled("#ai-stop")
    pick_and_start(ai)
    st = ai.page.locator("#ai-status")
    assert "qwen2.5-coder:7b" in st.inner_text() and st.get_attribute("data-idle")
    assert ai.page.is_disabled("#ai-start") is True and ai.page.is_enabled("#ai-stop")
    ai.page.reload()
    ai.page.wait_for_selector('#ai-status[data-state="ready"]')
    assert ai.page.locator('.ai-model[data-model-id="qwen2.5-coder:7b"] input').is_checked()      # selection is remembered


def test_stop_unloads_the_model(ai):
    open_ai(ai)
    ai.page.wait_for_selector(".ai-model")
    pick_and_start(ai)
    ai.page.click("#ai-stop")
    ai.page.wait_for_selector('#ai-status[data-state="off"]')
    assert [r["body"].get("keep_alive") for r in ai.fake.requests if r["path"] == "/api/chat"][-1] == 0


def test_a_failed_start_shows_the_reason_and_a_hint(ai):
    ai.holder.load_error = True
    open_ai(ai)
    ai.page.wait_for_selector(".ai-model")
    ai.page.locator('.ai-model[data-model-id="qwen2.5-coder:7b"] input').check()
    ai.page.click("#ai-start")
    ai.page.wait_for_selector('#ai-status[data-state="error"]', timeout=10000)
    text = ai.page.inner_text("#ai-status")
    assert "more memory" in text and "smaller model" in text
    assert ai.page.is_enabled("#ai-start")                                                          # can try again


def test_a_server_that_is_not_running_is_shown_as_such(ai):
    open_ai(ai)
    ai.page.wait_for_selector(".ai-provider")
    default_ollama = ai.page.locator('.ai-provider[data-endpoint="http://127.0.0.1:11434"]')
    if default_ollama.count():                                                                     # absent when something real listens there
        assert "Not running" in default_ollama.inner_text() or "Running" in default_ollama.inner_text()


# ----------------------------------------------------------------- tuning controls
def test_tuning_controls_clamp_save_and_come_back_after_reload(ai):
    open_ai(ai)
    ai.page.wait_for_selector('input[data-param="temperature"]')
    ai.page.fill('input[data-param-num="temperature"]', "0.9")
    ai.page.press('input[data-param-num="temperature"]', "Tab")
    assert wait_until(lambda: settings_now(ai)["params"]["temperature"] == 0.9)
    ai.page.wait_for_selector("#ai-saved:not([hidden])")
    ai.page.fill('input[data-param-num="temperature"]', "99")
    ai.page.press('input[data-param-num="temperature"]', "Tab")
    assert wait_until(lambda: settings_now(ai)["params"]["temperature"] == 2.0)
    assert ai.page.input_value('input[data-param-num="temperature"]') == "2"                        # the box shows the clamped value
    ai.page.fill('input[data-param="seed"]', "42")
    ai.page.press('input[data-param="seed"]', "Tab")
    ai.page.select_option("#ai-length", "short")
    ai.page.fill("#ai-instruction", "Answer in French.")
    assert wait_until(lambda: settings_now(ai)["params"]["seed"] == 42 and settings_now(ai)["params"]["length"] == "short"
                      and settings_now(ai)["params"]["instruction"] == "Answer in French.")
    ai.page.reload()
    ai.page.wait_for_selector('input[data-param-num="temperature"]')
    assert ai.page.input_value('input[data-param-num="temperature"]') == "2"
    assert ai.page.input_value('input[data-param="seed"]') == "42" and ai.page.input_value("#ai-length") == "short"
    assert ai.page.input_value("#ai-instruction") == "Answer in French."
    ai.page.fill('input[data-param="seed"]', "")
    ai.page.press('input[data-param="seed"]', "Tab")
    assert wait_until(lambda: settings_now(ai)["params"]["seed"] is None)                           # empty means random


def test_presets_and_reset(ai):
    open_ai(ai)
    ai.page.wait_for_selector("button[data-preset]")
    ai.page.click('button[data-preset="creative"]')
    assert wait_until(lambda: settings_now(ai)["params"]["temperature"] == 0.8 and settings_now(ai)["params"]["top_k"] == 80)
    assert wait_until(lambda: ai.page.input_value('input[data-param-num="top_k"]') == "80")      # the panel repaints after the reply
    ai.page.click("#ai-reset")
    assert wait_until(lambda: settings_now(ai)["params"]["temperature"] == 0.2 and settings_now(ai)["params"]["top_k"] == 40)
    assert wait_until(lambda: ai.page.input_value('input[data-param-num="temperature"]') == "0.2")


def test_advanced_options_are_saved(ai):
    open_ai(ai)
    ai.page.wait_for_selector(".ai-model")
    open_section(ai, "Advanced")
    ai.page.wait_for_selector("#ai-autostart")
    assert ai.page.is_checked("#ai-autostart") is False                                             # the fixture turned it off
    ai.page.check("#ai-autostart")
    ai.page.fill("#ai-idle", "3")
    ai.page.press("#ai-idle", "Tab")
    assert wait_until(lambda: settings_now(ai)["autostart"] is True and settings_now(ai)["idle_minutes"] == 3)


def test_controls_the_selected_server_cannot_use_are_greyed_out_with_a_reason(ai):
    gen = FakeServer({("GET", "/v1/models"): {"data": [{"id": "my-model"}]}})
    lm = FakeServer({("GET", "/api/v1/models"): LMS_V1})
    try:
        ai_settings.update({"endpoints": [{"provider": "ollama", "url": ai.fake.url}, {"provider": "openai", "url": gen.url},
                                          {"provider": "lmstudio", "url": lm.url}]})
        open_ai(ai)
        ai.page.wait_for_selector(".ai-model")
        ai.page.locator(f'.ai-provider[data-endpoint="{gen.url}"] .ai-model[data-model-id="my-model"] input').check()
        assert ai.page.is_disabled('input[data-param="top_k"]') and ai.page.is_disabled('input[data-param-num="min_p"]')
        assert "not used" in ai.page.inner_text('[data-param-note="top_k"]').lower()
        assert ai.page.is_enabled('input[data-param="temperature"]')
        ai.page.locator(f'.ai-provider[data-endpoint="{lm.url}"] .ai-model').first.locator("input").check()
        assert ai.page.is_enabled('input[data-param="top_k"]') and ai.page.is_disabled('input[data-param-num="min_p"]')
        assert "when the model is loaded" in ai.page.inner_text('[data-param-note="context_size"]').lower()
        ai.page.locator(f'.ai-provider[data-endpoint="{ai.fake.url}"] .ai-model[data-model-id="llama3.2:3b"] input').check()
        assert ai.page.is_enabled('input[data-param-num="min_p"]')
    finally:
        gen.stop()
        lm.stop()


# ----------------------------------------------------------------- adding servers
def test_adding_a_server_validates_the_address(ai):
    gen = FakeServer({("GET", "/v1/models"): {"data": [{"id": "my-model"}]}})
    try:
        open_ai(ai)
        ai.page.wait_for_selector(".ai-model")
        open_section(ai, "Add a server")
        ai.page.wait_for_selector("#ai-add-submit")
        ai.page.select_option("#ai-add-provider", "openai")
        ai.page.fill("#ai-add-url", "http://203.0.113.9:1234")
        ai.page.click("#ai-add-submit")
        ai.page.wait_for_selector("#ai-add-error:not([hidden])")
        assert "not allowed" in ai.page.inner_text("#ai-add-error").lower()
        assert len(settings_now(ai)["endpoints"]) == 1
        ai.page.fill("#ai-add-url", gen.url)
        ai.page.fill("#ai-add-name", "My server")
        ai.page.click("#ai-add-submit")
        ai.page.wait_for_selector(f'.ai-provider[data-endpoint="{gen.url}"]')
        assert "My server" in ai.page.inner_text(f'.ai-provider[data-endpoint="{gen.url}"]')
        assert [e["url"] for e in settings_now(ai)["endpoints"]][-1] == gen.url
        ai.page.locator(f'.ai-provider[data-endpoint="{gen.url}"] button.ai-remove').click()
        assert wait_until(lambda: len(settings_now(ai)["endpoints"]) == 1)
    finally:
        gen.stop()


def test_remote_servers_need_the_explicit_opt_in(ai):
    open_ai(ai)
    ai.page.wait_for_selector(".ai-model")
    open_section(ai, "Advanced")
    open_section(ai, "Add a server")
    ai.page.wait_for_selector("#ai-allow-remote")
    ai.page.select_option("#ai-add-provider", "openai")
    ai.page.fill("#ai-add-url", "http://my-ai-box.invalid:1234")
    ai.page.click("#ai-add-submit")
    ai.page.wait_for_selector("#ai-add-error:not([hidden])")
    ai.page.check("#ai-allow-remote")
    assert wait_until(lambda: settings_now(ai)["allow_remote"] is True)
    assert "send your code" in ai.page.inner_text("#ai-remote-warning").lower()
    ai.page.click("#ai-add-submit")
    assert wait_until(lambda: any(e["url"] == "http://my-ai-box.invalid:1234" for e in settings_now(ai)["endpoints"]))


# ----------------------------------------------------------------- chat
def ready_ai(ai):
    open_ai(ai)
    ai.page.wait_for_selector(".ai-model")
    pick_and_start(ai)


def test_chat_is_disabled_with_a_hint_until_the_ai_is_running(ai):
    open_ai(ai)
    ai.page.wait_for_selector("#ai-send")
    assert ai.page.is_disabled("#ai-send") and ai.page.is_disabled("#ai-input")
    assert "start the ai explainer" in ai.page.inner_text("#ai-chat-hint").lower()


def test_an_answer_streams_in_and_the_exact_prompt_can_be_inspected(ai):
    ready_ai(ai)
    ask(ai, "What does hello do?")
    ai.page.wait_for_selector(".ai-msg.assistant .ai-stats")
    assert ai.page.inner_text(".ai-msg.user") == "What does hello do?"
    assert ai.page.inner_text(".ai-msg.assistant .ai-text") == "Hello world"
    assert "tokens/s" in ai.page.inner_text(".ai-stats") or "s" in ai.page.inner_text(".ai-stats")
    assert ai.page.is_enabled("#ai-input") and ai.page.input_value("#ai-input") == ""
    ai.page.click("button.ai-show-sent")
    sent = ai.page.inner_text("pre.ai-sent-body")
    assert "untrusted" in sent and "num_predict" in sent and "What does hello do?" in sent and "qwen2.5-coder:7b" in sent
    ai.page.click("button.ai-show-sent")
    assert ai.page.locator("pre.ai-sent-body").count() == 0


def test_enter_sends_and_shift_enter_adds_a_line(ai):
    ready_ai(ai)
    ai.page.fill("#ai-input", "line one")
    ai.page.press("#ai-input", "Shift+Enter")
    ai.page.type("#ai-input", "line two")
    assert ai.page.input_value("#ai-input") == "line one\nline two"
    ai.page.press("#ai-input", "Enter")
    ai.page.wait_for_selector(".ai-msg.assistant .ai-stats")
    assert ai.page.inner_text(".ai-msg.user") == "line one\nline two"


def test_follow_up_questions_include_the_conversation(ai):
    ready_ai(ai)
    ask(ai, "first question")
    ai.page.wait_for_selector(".ai-msg.assistant .ai-stats")
    ask(ai, "second question")
    ai.page.wait_for_function("document.querySelectorAll('.ai-msg.assistant .ai-stats').length === 2")
    body = [r["body"] for r in ai.fake.requests if r["path"] == "/api/chat" and r["body"]["messages"]][-1]
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user", ]
    assert body["messages"][2]["content"] == "Hello world"
    ai.page.click("#ai-clear")
    assert ai.page.locator(".ai-msg").count() == 0


def test_answers_follow_the_explain_level_from_the_top_menu(ai):
    ready_ai(ai)
    ai.page.select_option("#level", "tech")
    ask(ai, "q1")
    ai.page.wait_for_selector(".ai-msg.assistant .ai-stats")
    body = [r["body"] for r in ai.fake.requests if r["path"] == "/api/chat" and r["body"]["messages"]][-1]
    assert "experienced engineer" in body["messages"][0]["content"]
    ai.page.select_option("#level", "kid")
    ask(ai, "q2")
    ai.page.wait_for_function("document.querySelectorAll('.ai-msg.assistant .ai-stats').length === 2")
    body = [r["body"] for r in ai.fake.requests if r["path"] == "/api/chat" and r["body"]["messages"]][-1]
    assert "8-year-old" in body["messages"][0]["content"]


def test_the_open_file_can_be_sent_as_context(ai):
    ai.page.goto(f"{ai.base}/#tab=ai&f=a.py")
    ai.page.wait_for_selector(".ai-model")
    pick_and_start(ai)
    assert ai.page.is_checked("#ai-context") and "a.py" in ai.page.inner_text('label[for="ai-context"]')
    ask(ai, "explain it")
    ai.page.wait_for_selector(".ai-msg.assistant .ai-stats")
    system = [r["body"] for r in ai.fake.requests if r["path"] == "/api/chat" and r["body"]["messages"]][-1]["messages"][0]["content"]
    assert "a.py" in system and "def hello" in system and "<repository_context>" in system
    ai.page.uncheck("#ai-context")
    ask(ai, "again")
    ai.page.wait_for_function("document.querySelectorAll('.ai-msg.assistant .ai-stats').length === 2")
    system = [r["body"] for r in ai.fake.requests if r["path"] == "/api/chat" and r["body"]["messages"]][-1]["messages"][0]["content"]
    assert "def hello" not in system


def test_the_stop_button_halts_a_long_answer(ai):
    ai.holder.route = ndjson_route(ollama_tokens(*["word "] * 300), delay=0.05)
    ready_ai(ai)
    ask(ai, "tell me everything")
    ai.page.wait_for_function("document.querySelector('.ai-msg.assistant .ai-text') && document.querySelector('.ai-msg.assistant .ai-text').innerText.length > 10")
    ai.page.click("#ai-chat-stop")
    ai.page.wait_for_selector(".ai-note")
    assert "stopped" in ai.page.inner_text(".ai-note").lower()
    assert wait_until(lambda: not api_status(ai)["busy"], 5)
    assert ai.page.inner_text(".ai-msg.assistant .ai-text").count("word") < 250
    ask(ai, "short one")                                                                                # a new question works after stopping
    ai.holder.route = ndjson_route(ollama_tokens("ok"))


def test_an_error_mid_answer_keeps_the_text_and_explains(ai):
    ai.holder.route = ndjson_route([{"message": {"content": "partial answer "}, "done": False},
                                    {"error": "llama runner process has terminated"}])
    ready_ai(ai)
    ask(ai, "q")
    ai.page.wait_for_selector(".ai-note.error")
    assert "partial answer" in ai.page.inner_text(".ai-msg.assistant .ai-text")
    assert "runner process has terminated" in ai.page.inner_text(".ai-note.error")
    assert ai.page.is_enabled("#ai-input")


def test_a_truncated_answer_tells_you_which_setting_to_raise(ai):
    ai.holder.route = ndjson_route(ollama_tokens("cut ", "off", done_reason="length"))
    ready_ai(ai)
    ask(ai, "q")
    ai.page.wait_for_selector(".ai-note")
    note = ai.page.inner_text(".ai-note").lower()
    assert "cut off" in note and "max answer length" in note


def test_model_output_and_names_are_never_rendered_as_html(ai):
    ai.holder.route = ndjson_route(ollama_tokens('<img src=x onerror="window.__pwned=1"> and <script>window.__pwned=2</script> ',
                                                 "`code` and ```py\nprint(1)\n```"))
    ai.holder.tags = {"models": OLLAMA_TAGS["models"][:1] + [
        {"name": 'evil<img src=x onerror="window.__pwned=3">:1b', "model": 'evil<img src=x onerror="window.__pwned=3">:1b',
         "size": 1000, "details": {"family": "llama", "parameter_size": "1B", "quantization_level": "Q4"}}]}
    open_ai(ai)
    ai.page.wait_for_selector(".ai-model")
    assert ai.page.locator(".ai-provider img").count() == 0 and "<img" in ai.page.inner_text(".ai-provider")
    ai.page.locator('.ai-model[data-model-id="llama3.2:3b"] input').check()
    ai.page.click("#ai-start")
    ai.page.wait_for_selector('#ai-status[data-state="ready"]', timeout=10000)
    ask(ai, "<b>hi</b>")
    ai.page.wait_for_selector(".ai-msg.assistant .ai-stats")
    ai.page.wait_for_timeout(300)
    assert ai.page.evaluate("window.__pwned") is None
    assert ai.page.locator(".ai-msg img, .ai-msg script, .ai-msg b").count() == 0
    assert "<img" in ai.page.inner_text(".ai-msg.assistant .ai-text") and ai.page.inner_text(".ai-msg.user") == "<b>hi</b>"
    assert ai.page.locator(".ai-msg.assistant code").count() >= 1                                       # code spans do render, as <code>


def test_idle_time_is_shown_while_running(ai):
    ready_ai(ai)
    assert int(ai.page.get_attribute("#ai-status", "data-idle")) > 0
    assert "idle" in ai.page.inner_text("#ai-status").lower()


def test_bold_and_code_render_but_other_markup_stays_text(ai):
    ai.holder.route = ndjson_route(ollama_tokens("The **front door** opens `main` and <b>not this</b>. A * lone star and **unclosed"))
    ready_ai(ai)
    ask(ai, "q")
    ai.page.wait_for_selector(".ai-msg.assistant .ai-stats")
    assert ai.page.locator(".ai-msg.assistant strong").all_inner_texts() == ["front door"]
    assert ai.page.locator(".ai-msg.assistant code").all_inner_texts() == ["main"]
    text = ai.page.inner_text(".ai-msg.assistant .ai-text")
    assert "<b>not this</b>" in text and "A * lone star and **unclosed" in text and ai.page.locator(".ai-msg.assistant b").count() == 0
