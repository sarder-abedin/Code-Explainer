"""Browser tests for the "Explain with AI" buttons and the saved-explanations cache."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from ai_fakes import FakeServer, ndjson_route, ollama_routes, ollama_tokens
from code_explainer.ai import settings as ai_settings
from code_explainer.core import analyze_repo
from code_explainer.report import render_html
from code_explainer.server import Session, make_handler
from test_ui_ai import ai, api_status, browser, open_ai, open_section, pick_and_start, ready_ai, settings_now, wait_until  # noqa: F401

sync_api = pytest.importorskip("playwright.sync_api")


def model_calls(ai):
    return [r["body"] for r in ai.fake.requests if r["path"] == "/api/chat" and r["body"]["messages"]]


def goto_file(ai, path="a.py", tab="overview"):
    ai.page.goto(f"{ai.base}/#tab={tab}&f={path}")
    ai.page.wait_for_selector("#detail:not([hidden])")


def explain_file(ai):
    btn = ai.page.locator('.aix-btn[data-kind="file"]')
    btn.wait_for()
    btn.click()
    ai.page.wait_for_selector('.aix-box .aix-stats')


def box_text(ai, nth=0):
    return ai.page.locator(".aix-box .aix-text").nth(nth).inner_text()


# ----------------------------------------------------------------- files, symbols and the saved-answer cache
def test_a_file_can_be_explained_and_the_second_visit_is_instant(ai):
    ready_ai(ai)
    goto_file(ai)
    assert ai.page.locator('.aix-btn[data-kind="file"]').inner_text().strip().startswith("✨")
    explain_file(ai)
    assert box_text(ai) == "Hello world" and ai.page.locator(".aix-cached").count() == 0
    assert ai.page.locator(".aix-box .ai-source").count() >= 1                          # the code it was based on
    assert "tokens" in ai.page.inner_text(".aix-stats") or "s" in ai.page.inner_text(".aix-stats")
    assert len(model_calls(ai)) == 1
    ai.page.reload()                                                                     # a fresh page: the answer is on disk, not in memory
    ai.page.wait_for_selector("#detail:not([hidden])")
    explain_file(ai)
    assert box_text(ai) == "Hello world" and "saved answer" in ai.page.inner_text(".aix-cached").lower()
    assert len(model_calls(ai)) == 1                                                     # the model was not asked again


def test_regenerate_asks_the_model_again(ai):
    ready_ai(ai)
    goto_file(ai)
    explain_file(ai)
    ai.holder.route = ndjson_route(ollama_tokens("A ", "different ", "take."))
    ai.page.click(".aix-regen")
    ai.page.wait_for_function("(() => { const e = document.querySelector('.aix-box .aix-text'); return !!e && e.innerText === 'A different take.'; })()")
    assert len(model_calls(ai)) == 2 and ai.page.locator(".aix-cached").count() == 0


def test_a_saved_explanation_works_after_the_ai_is_stopped(ai):
    ready_ai(ai)
    goto_file(ai)
    explain_file(ai)
    ai.page.goto(f"{ai.base}/#tab=ai")
    ai.page.click("#ai-stop")
    ai.page.wait_for_selector('#ai-status[data-state="off"]')
    goto_file(ai)
    ai.page.reload()
    ai.page.wait_for_selector("#detail:not([hidden])")
    ai.page.locator('.aix-btn[data-kind="file"]').click()
    ai.page.wait_for_selector(".aix-cached")
    assert box_text(ai) == "Hello world" and api_status(ai)["state"] == "off"


def test_functions_have_their_own_buttons_with_their_own_code(ai):
    ready_ai(ai)
    goto_file(ai)
    btn = ai.page.locator('.aix-btn[data-kind="symbol"]')
    assert btn.count() == 1                                                              # `hello` (the module itself has no button)
    btn.click()
    ai.page.wait_for_selector(".aix-box .aix-stats")
    sent = model_calls(ai)[-1]["messages"]
    assert "def hello" in sent[0]["content"] and "`hello`" in sent[-1]["content"]
    assert ai.page.locator(".symwrap .aix-box").count() == 1                              # the answer sits under the function it explains


def test_an_explanation_follows_the_explain_level_and_each_level_is_saved_separately(ai):
    ready_ai(ai)
    goto_file(ai)
    explain_file(ai)
    assert "8-year-old" in model_calls(ai)[-1]["messages"][0]["content"]
    ai.page.select_option("#level", "tech")
    ai.page.locator('.aix-btn[data-kind="file"]').click()
    ai.page.wait_for_selector(".aix-box .aix-stats")
    assert "experienced engineer" in model_calls(ai)[-1]["messages"][0]["content"] and len(model_calls(ai)) == 2
    ai.page.select_option("#level", "kid")
    ai.page.wait_for_selector(".aix-box .aix-stats")                                     # the kid-level answer is still on screen
    assert len(model_calls(ai)) == 2


def test_line_breaks_in_an_explanation_are_kept(ai):
    ai.holder.route = ndjson_route(ollama_tokens("First point.\n", "- second point\n", "- third point"))
    ready_ai(ai)
    goto_file(ai)
    explain_file(ai)
    assert box_text(ai).split("\n") == ["First point.", "- second point", "- third point"]


# ----------------------------------------------------------------- flow steps and changes
def test_a_flow_step_can_be_explained_with_who_called_it(ai):
    ready_ai(ai)
    ai.page.goto(f"{ai.base}/#tab=flow")
    ai.page.wait_for_selector(".frow")
    ai.page.click("text=Step through")
    assert ai.page.locator(".card.step .aix-btn").count() == 0                           # step 1 is the file's top-level code: nothing to explain on its own
    ai.page.click("text=Next ▶")
    ai.page.click("text=Next ▶")                                                         # step 3: hello, called by main
    ai.page.wait_for_selector(".card.step")
    ai.page.locator('.card.step .aix-btn[data-kind="flow"]').click()
    ai.page.wait_for_selector(".card.step .aix-stats")
    sent = model_calls(ai)[-1]["messages"]
    assert "called by `main`" in sent[-1]["content"] and "return hello()" in sent[0]["content"]


def test_a_change_can_be_explained(repo, tmp_path, browser):
    session = Session(repo, "r", "HEAD~1", "HEAD")
    holder = SimpleNamespace(route=ndjson_route(ollama_tokens("It ", "changed.")))

    def chat(h, b):
        return h._send(200, {"done": True}) if b.get("messages") == [] else holder.route(h, b)
    fake = FakeServer(ollama_routes({("POST", "/api/chat"): chat}))
    ai_settings.update({"endpoints": [{"provider": "ollama", "url": fake.url}], "autostart": False})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.set_default_timeout(8000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        session.ai.lifecycle.start("ollama", fake.url, "qwen2.5-coder:7b")
        assert session.ai.lifecycle.wait(10)["state"] == "ready"
        page.goto(f"http://127.0.0.1:{httpd.server_port}/#tab=changes")
        page.wait_for_selector(".card.chg")
        btn = page.locator('.card.chg .aix-btn[data-kind="change"]').first
        btn.click()
        page.wait_for_selector(".card.chg .aix-stats")
        sent = [r["body"] for r in fake.requests if r["path"] == "/api/chat" and r["body"]["messages"]][-1]["messages"]
        assert "@@" in sent[0]["content"] and "change being reviewed" in sent[-1]["content"]
        assert page.inner_text(".card.chg .aix-text") == "It changed." and errors == []
    finally:
        page.close()
        session.ai.shutdown()
        httpd.shutdown()
        httpd.server_close()
        fake.stop()


# ----------------------------------------------------------------- when the AI is not running
def test_without_a_running_ai_the_button_explains_what_to_do(ai):
    goto_file(ai)
    ai.page.locator('.aix-btn[data-kind="file"]').click()
    ai.page.wait_for_selector(".aix-note.error")
    assert "not running" in ai.page.inner_text(".aix-note").lower() or "start" in ai.page.inner_text(".aix-note").lower()
    assert ai.page.locator(".aix-start").count() == 0                                    # no model has been chosen yet
    ai.page.click(".aix-open")
    ai.page.wait_for_selector("#ai-status")
    assert "tab=ai" in ai.page.evaluate("location.hash")


def test_start_and_explain_in_one_click_when_a_model_is_remembered(ai):
    ai_settings.update({"selected": {"provider": "ollama", "endpoint": ai.fake.url, "model": "qwen2.5-coder:7b"}})
    goto_file(ai)
    ai.page.locator('.aix-btn[data-kind="file"]').click()
    ai.page.wait_for_selector(".aix-start")
    assert "qwen2.5-coder:7b" in ai.page.inner_text(".aix-start")
    ai.page.click(".aix-start")
    ai.page.wait_for_selector(".aix-box .aix-stats", timeout=15000)
    assert box_text(ai) == "Hello world" and api_status(ai)["state"] == "ready"


# ----------------------------------------------------------------- stopping, failing, truncating
def test_the_stop_button_halts_an_explanation(ai):
    ai.holder.route = ndjson_route(ollama_tokens(*["word "] * 300), delay=0.05)
    ready_ai(ai)
    goto_file(ai)
    ai.page.locator('.aix-btn[data-kind="file"]').click()
    ai.page.wait_for_function("(() => { const e = document.querySelector('.aix-text'); return !!e && e.innerText.length > 10; })()")
    ai.page.click(".aix-stop")
    ai.page.wait_for_selector(".aix-note")
    assert "stopped" in ai.page.inner_text(".aix-note").lower()
    assert wait_until(lambda: not api_status(ai)["busy"], 5)
    assert ai.page.locator(".aix-btn, .aix-regen").count() >= 1                           # it can be run again


def test_failed_and_truncated_explanations_are_shown_honestly_and_never_saved(ai):
    ai.holder.route = ndjson_route([{"message": {"content": "partial "}, "done": False}, {"error": "llama runner process has terminated"}])
    ready_ai(ai)
    goto_file(ai)
    ai.page.locator('.aix-btn[data-kind="file"]').click()
    ai.page.wait_for_selector(".aix-note.error")
    assert "terminated" in ai.page.inner_text(".aix-note.error") and "partial" in box_text(ai)
    ai.holder.route = ndjson_route(ollama_tokens("cut ", "off", done_reason="length"))
    ai.page.click(".aix-box .aix-btn")                                                   # after a failure the button says "Try again"
    ai.page.wait_for_selector(".aix-note.warn")
    assert "cut off" in ai.page.inner_text(".aix-note.warn").lower() and "max answer length" in ai.page.inner_text(".aix-note.warn").lower()
    assert json.loads(urllib.request.urlopen(ai.base + "/api/ai/cache").read())["entries"] == 0


def test_explanations_are_never_rendered_as_html(ai):
    ai.holder.route = ndjson_route(ollama_tokens('<img src=x onerror="window.__pwned=1"> **bold** `a.py:3` and nope.py:9'))
    ready_ai(ai)
    goto_file(ai)
    ai.page.locator('.aix-btn[data-kind="file"]').click()
    ai.page.wait_for_selector(".aix-box .aix-stats")
    ai.page.wait_for_timeout(300)
    assert ai.page.evaluate("window.__pwned") is None
    assert ai.page.locator(".aix-box img").count() == 0 and "<img" in box_text(ai)
    assert ai.page.locator(".aix-box strong").all_inner_texts() == ["bold"]
    assert ai.page.locator(".aix-box a.ai-cite").all_inner_texts() == ["a.py:3"] and ai.page.locator(".aix-box span.ai-cite.bad").count() == 1


# ----------------------------------------------------------------- managing saved explanations
def test_saved_explanations_can_be_counted_and_cleared(ai):
    ready_ai(ai)
    goto_file(ai)
    explain_file(ai)
    open_ai(ai)
    ai.page.wait_for_selector(".ai-model")
    open_section(ai, "Advanced")
    assert "1" in ai.page.inner_text("#ai-cache-info") and "saved" in ai.page.inner_text("#ai-cache-info").lower()
    ai.page.click("#ai-cache-clear")
    assert wait_until(lambda: "0" in ai.page.inner_text("#ai-cache-info") and "1 " not in ai.page.inner_text("#ai-cache-info"))
    goto_file(ai)
    ai.page.reload()
    ai.page.wait_for_selector("#detail:not([hidden])")
    explain_file(ai)
    assert len(model_calls(ai)) == 2 and ai.page.locator(".aix-cached").count() == 0


def test_a_saved_report_has_no_ai_buttons(tmp_path, browser):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    out = tmp_path / "r.html"
    out.write_text(render_html(analyze_repo(tmp_path, "x")), encoding="utf-8")
    page = browser.new_page()
    page.set_default_timeout(6000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(out.as_uri() + "#tab=overview&f=a.py")
    page.wait_for_selector("#detail:not([hidden])")
    assert page.locator(".aix-btn").count() == 0 and errors == []
    page.close()
