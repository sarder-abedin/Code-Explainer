"""Browser tests for AI explanations written into a static report by `analyze --ai`."""
import os

import pytest

from code_explainer.ai import batch
from code_explainer.ai.batch import BatchResult
from code_explainer.core import analyze_repo
from code_explainer.report import render_html

sync_api = pytest.importorskip("playwright.sync_api")

EVIL = "</script><script>window.pwned=1</script>"
LIB = "Loads the **items** and sums them with `load`. See app/lib.py:2. " + EVIL
MAIN = "The program starts here."


def item(kind, path, text):
    return {"kind": kind, "path": path, "title": "T", "text": text}


@pytest.fixture
def page_for(tmp_path):
    state = {}

    def open_report(data, hash_=""):
        out = tmp_path / "r.html"
        out.write_text(render_html(data), encoding="utf-8")
        if "p" not in state:
            state["pw"] = sync_api.sync_playwright().start()
            try:
                exe = os.environ.get("CHROMIUM_PATH")
                state["browser"] = (state["pw"].chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe
                                    else state["pw"].chromium.launch())
            except Exception as e:
                state["pw"].stop()
                pytest.skip(str(e))
            state["p"] = state["browser"].new_page(viewport={"width": 1300, "height": 900})
            state["errors"] = []
            state["p"].on("pageerror", lambda e: state["errors"].append(str(e)))
        state["p"].goto("about:blank")           # a same-address visit would only be a hash change, not a reload
        state["p"].goto(out.as_uri() + hash_)
        return state["p"], state["errors"]
    yield open_report
    if "p" in state:
        state["browser"].close()
        state["pw"].stop()


def baked(repo, items, **kw):
    data = analyze_repo(repo, "demo", **kw)
    batch.attach(data, BatchResult(items=items), provider="ollama", model="llama3.2:3b", level="kid")
    return data


def test_a_file_shows_its_ai_explanation_read_only_and_labelled(repo, page_for):
    data = baked(repo, {"file|app/lib.py": item("file", "app/lib.py", LIB)})
    page, errors = page_for(data, "#tab=overview&f=app/lib.py")
    page.wait_for_selector("#detail:not([hidden]) .aix-baked")
    box = page.locator(".aix-baked")
    assert box.count() == 1
    assert "AI explanation" in box.locator(".aix-label").inner_text()
    head = box.locator(".aix-head").inner_text()
    assert "llama3.2:3b" in head and "beginner-friendly" in head and "mistakes" in head
    assert box.locator(".aix-text strong").inner_text() == "items" and box.locator(".aix-text code").first.inner_text() == "load"
    assert "</script>" in box.locator(".aix-text").inner_text()           # shown as plain text, never run
    assert page.evaluate("window.pwned") is None
    assert page.locator(".aix-btn, .aix-regen, .aix-start").count() == 0      # a file can not ask a model anything
    assert errors == []


def test_a_file_without_one_shows_nothing_extra(repo, page_for):
    data = baked(repo, {"file|app/lib.py": item("file", "app/lib.py", LIB)})
    page, errors = page_for(data, "#tab=overview&f=app/main.py")
    page.wait_for_selector("#detail:not([hidden])")
    assert page.locator(".aix-baked").count() == 0 and errors == []


def test_a_report_without_any_is_unchanged(repo, page_for):
    page, errors = page_for(analyze_repo(repo, "demo"), "#tab=overview&f=app/lib.py")
    page.wait_for_selector("#detail:not([hidden])")
    assert page.locator(".aix, .aix-baked").count() == 0 and errors == []


def test_changes_show_theirs_too(repo, page_for):
    data = baked(repo, {"change|app/lib.py": item("change", "app/lib.py", "A new guard was added."),
                        "file|app/main.py": item("file", "app/main.py", MAIN)}, base="HEAD~1")
    page, errors = page_for(data, "#tab=changes")
    page.wait_for_selector(".chg")
    cards = page.locator(".chg .aix-baked")
    assert cards.count() == 1 and "A new guard was added." in cards.first.inner_text()
    assert "app/lib.py" in page.locator(".chg", has=page.locator(".aix-baked")).first.inner_text()
    assert errors == []


def test_the_ai_tab_says_what_the_report_contains(repo, page_for):
    data = baked(repo, {"file|app/lib.py": item("file", "app/lib.py", LIB), "file|app/main.py": item("file", "app/main.py", MAIN)})
    page, errors = page_for(data, "#tab=ai")
    page.wait_for_selector(".ai-static")
    text = page.locator(".ai-static").inner_text()
    assert "contains 2 explanations" in text and "llama3.2:3b" in text
    page, errors = page_for(analyze_repo(repo, "demo"), "#tab=ai")
    page.wait_for_selector(".ai-static")
    assert "analyze <repo> --ai" in page.locator(".ai-static").inner_text() and errors == []
