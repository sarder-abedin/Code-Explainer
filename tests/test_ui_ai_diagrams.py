"""AI explanations on flowchart boxes, parts of the map and arrows between them, in `serve` and in a saved report."""
import shutil

import pytest

from code_explainer.ai import batch
from code_explainer.ai.batch import BatchResult
from code_explainer.core import analyze_repo
from code_explainer.report import render_html
from conftest import write
from test_ui_ai import ai, api_status, browser, open_ai, pick_and_start, ready_ai  # noqa: F401
from test_ui_ai_explain import model_calls

sync_api = pytest.importorskip("playwright.sync_api")

SHOP = {
    "shop/__init__.py": "",
    "shop/web/__init__.py": "",
    "shop/web/views.py": '"""Pages."""\nfrom shop.core import pricing\n\n\ndef home():\n    return pricing.total([1])\n',
    "shop/core/__init__.py": "",
    "shop/core/pricing.py": '"""Price maths."""\n\n\ndef total(items):\n    if not items:\n        return 0\n    return sum(items)\n',
    "shop/db/__init__.py": "",
    "shop/db/store.py": "def load():\n    return 1\n",
    "shop/core/orders.py": "from shop.db import store\n\n\ndef order():\n    return store.load()\n",
}
TOTAL = "shop/core/pricing.py::total"


def shop(ai, files=None):
    """Replace the fixture's little project with a shop that has three folders (so the map has three parts)."""
    for old in ("a.py", "main.py"):
        (ai.proj / old).unlink()
    for pkg in ("pkg1", "pkg2"):
        shutil.rmtree(ai.proj / pkg)
    write(ai.proj, files or SHOP)
    ai.session.refresh()


def open_chart(ai):
    ai.page.goto("about:blank")           # a same-address visit would only be a hash change, not a reload
    ai.page.goto(f"{ai.base}/#tab=overview&f=shop/core/pricing.py&chart={TOTAL}")
    ai.page.wait_for_selector("#chart .fc-node")


def click_kind(ai, kind):
    ai.page.locator(f"#chart .fc-node.fc-{kind}").first.click()


def asked(ai):
    return model_calls(ai)[-1]["messages"]


# ----------------------------------------------------------------- flowchart boxes
def test_a_box_can_be_explained_and_the_prompt_is_about_that_box(ai):
    ready_ai(ai)
    shop(ai)
    open_chart(ai)
    click_kind(ai, "decision")
    btn = ai.page.locator('#chart .aix-btn[data-kind="flowstep"]')
    assert "box" in btn.inner_text().lower()
    btn.click()
    ai.page.wait_for_selector("#chart .aix-stats")
    assert ai.page.locator("#chart .aix-text").inner_text() == "Hello world"
    sent = asked(ai)
    assert "if not items:" in sent[0]["content"] and "this box of its flowchart" in sent[-1]["content"]


def test_the_start_and_end_boxes_have_nothing_to_explain(ai):
    ready_ai(ai)
    shop(ai)
    open_chart(ai)
    click_kind(ai, "start")
    assert ai.page.locator("#chart .fc-info .aix-btn").count() == 0
    assert ai.page.locator('#chart .aix-btn[data-kind="flowstep"]').count() == 0


def test_each_box_keeps_its_own_answer(ai):
    ready_ai(ai)
    shop(ai)
    open_chart(ai)
    click_kind(ai, "decision")
    ai.page.locator('#chart .aix-btn[data-kind="flowstep"]').click()
    ai.page.wait_for_selector("#chart .aix-stats")
    click_kind(ai, "return")
    assert ai.page.locator("#chart .aix-text").count() == 0                       # a different box, not asked yet
    assert ai.page.locator('#chart .aix-btn[data-kind="flowstep"]').count() == 1
    click_kind(ai, "decision")
    assert ai.page.locator("#chart .aix-text").inner_text() == "Hello world"      # the first box still has its answer
    assert len(model_calls(ai)) == 1


def test_before_a_box_is_picked_the_whole_function_can_be_explained(ai):
    ready_ai(ai)
    shop(ai)
    open_chart(ai)
    btn = ai.page.locator('#chart .aix-btn[data-kind="symbol"]')
    assert "walk me through" in btn.inner_text().lower()
    btn.click()
    ai.page.wait_for_selector("#chart .aix-stats")
    assert "Explain `total`" in asked(ai)[-1]["content"]


def test_following_a_source_from_a_flowchart_answer_closes_the_chart_and_shows_the_code(ai):
    ready_ai(ai)
    shop(ai)
    open_chart(ai)
    click_kind(ai, "decision")
    ai.page.locator('#chart .aix-btn[data-kind="flowstep"]').click()
    ai.page.wait_for_selector("#chart .aix-stats")
    ai.page.locator("#chart .ai-source").first.click()
    assert ai.page.locator("#chart").count() == 0
    ai.page.wait_for_selector("#detail [data-panel=code] .code .hl")
    assert "shop/core/pricing.py" in ai.page.locator("#detail .dhead .t").inner_text()


# ----------------------------------------------------------------- parts and arrows of the map
def open_map(ai, comp=""):
    ai.page.goto("about:blank")
    ai.page.goto(f"{ai.base}/#tab=map" + (f"&comp={comp}" if comp else ""))
    ai.page.wait_for_selector("#arch .ar-info")


def test_a_part_of_the_map_can_be_explained(ai):
    ready_ai(ai)
    shop(ai)
    open_map(ai, "shop/core")
    btn = ai.page.locator('#arch .ar-info .aix-btn[data-kind="part"]')
    assert "part" in btn.inner_text().lower()
    btn.click()
    ai.page.wait_for_selector("#arch .ar-info .aix-stats")
    assert ai.page.locator("#arch .ar-info .aix-text").inner_text() == "Hello world"
    assert "Facts about this part" in asked(ai)[0]["content"] and "`core`" in asked(ai)[-1]["content"]


def test_an_arrow_between_parts_can_be_explained(ai):
    ready_ai(ai)
    shop(ai)
    open_map(ai)
    ai.page.locator('#arch .ar-edge[data-from="shop/web"][data-to="shop/core"] .ar-hit').dispatch_event("click")
    btn = ai.page.locator('#arch .ar-info .aix-btn[data-kind="link"]')
    assert "arrow" in btn.inner_text().lower()
    btn.click()
    ai.page.wait_for_selector("#arch .ar-info .aix-stats")
    assert "from shop.core import pricing" in asked(ai)[0]["content"]


def test_a_part_answer_stays_with_that_part(ai):
    ready_ai(ai)
    shop(ai)
    open_map(ai, "shop/core")
    ai.page.locator('#arch .ar-info .aix-btn[data-kind="part"]').click()
    ai.page.wait_for_selector("#arch .ar-info .aix-stats")
    ai.page.locator('#arch .ar-comp[data-id="shop/web"]').click()
    assert ai.page.locator("#arch .ar-info .aix-text").count() == 0 and ai.page.locator('#arch .ar-info .aix-btn[data-kind="part"]').count() == 1


def test_things_outside_the_project_have_no_ai_button(ai):
    ready_ai(ai)
    shop(ai, {**SHOP, "shop/db/store.py": "import sqlite3\n\n\ndef load():\n    return sqlite3.connect('x')\n"})
    open_map(ai)
    ai.page.locator("#arch .ar-ext").first.click()
    assert ai.page.locator("#arch .ar-info .badge", has_text="outside the project").count() == 1
    assert ai.page.locator("#arch .ar-info .aix-btn").count() == 0


# ----------------------------------------------------------------- a saved report
def static_page(browser, tmp_path, data, hash_):
    out = tmp_path / "r.html"
    out.write_text(render_html(data), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.set_default_timeout(6000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(out.as_uri() + hash_)
    return page, errors


def test_a_saved_report_shows_a_baked_part_explanation_and_no_buttons_on_diagrams(tmp_path, browser):  # noqa: F811
    write(tmp_path, SHOP)
    data = analyze_repo(tmp_path, "shop")
    item = {"kind": "part", "path": "shop/core", "title": "Explain the part `core`", "text": "This part does the **prices**."}
    batch.attach(data, BatchResult(items={"part|shop/core": item}), provider="ollama", model="llama3.2:3b", level="kid")
    page, errors = static_page(browser, tmp_path, data, "#tab=map&comp=shop/core")
    page.wait_for_selector("#arch .ar-info")
    assert page.locator("#arch .ar-info .aix-baked .aix-text strong").inner_text() == "prices"
    assert page.locator("#arch .ar-info .aix-btn").count() == 0
    page.close()
    page, errors2 = static_page(browser, tmp_path, data, f"#tab=overview&f=shop/core/pricing.py&chart={TOTAL}")
    page.wait_for_selector("#chart .fc-node")
    assert page.locator("#chart .aix, #chart .aix-btn").count() == 0 and not errors and not errors2
    page.close()
